"""Generate each radio batch using the latest owner ratings and feedback.

Radio queues are open-ended. Explicit playlists remain fixed.
No Telegram-native skip/completion events are available.
"""
from __future__ import annotations

from music_bot.recommend import wave
from music_bot.wave_v2 import wave_v2
from music_bot.storage import Library, Track


DYNAMIC_MODES = frozenset(("balanced", "discover", "favorites"))


def select_wave_batch(db: Library, mode: str, count: int = 10,
                      *, queue_id: int | None = None) -> list[Track]:
    if mode not in DYNAMIC_MODES:
        raise ValueError("Unknown radio mode")
    if not 1 <= count <= 10:
        raise ValueError("Batch size must be 1–10")
    # Don't silently exclude the oldest tracks when the library grows.
    candidates = db.all_tracks(limit=10000)
    if queue_id is not None:
        filters = db.get_queue_filters(queue_id)
        if filters["liked_only"]:
            liked = db.liked_ids()
            candidates = [t for t in candidates if t.id in liked]
        if filters["min_rating"]:
            ratings = db.ratings()
            candidates = [t for t in candidates
                          if ratings.get(t.id, 0) >= filters["min_rating"]]
        if filters["no_repeat_today"]:
            already_sent = db.sent_today_ids()
            candidates = [t for t in candidates if t.id not in already_sent]
    if not candidates:
        return []

    # Avoid an immediate replay of the most recently sent three songs,
    # but never dead-end a tiny personal library.
    no_repeat = db.latest_sent_ids(3)
    filtered = [t for t in candidates if t.id not in no_repeat]
    pool = filtered if len(filtered) >= min(count, 3) else candidates
    liked_ids = db.liked_ids()
    disliked_ids = db.disliked_ids()
    ratings = db.ratings()
    playback = db.playback_stats()
    # Existing preferences and playlists give free, zero-click taste signals.
    seeds = {tid for tid in liked_ids | {tid for tid, rating in ratings.items() if rating >= 8}
             if tid not in disliked_ids and not (
                 playback.get(tid, (0, 0))[0] >= 3
                 and playback.get(tid, (0, 0))[0] >=
                 2 * playback.get(tid, (0, 0))[1] + 3
             )}
    signals = db.recommendation_signals(seeds)
    prefs = {
        "liked_ids": liked_ids,
        "disliked_ids": disliked_ids,
        "recent_ids": db.recent_ids(60),
        "ratings": ratings,
        "playback": playback,
        **signals,
        "mode": mode,
        "count": count,
    }
    result = (wave_v2(pool, prefs["liked_ids"], prefs["disliked_ids"],
                      ratings=prefs["ratings"], playback=prefs["playback"],
                      recent_ids=prefs["recent_ids"], count=count,
                      exposure=prefs["exposure"],
                      playlist_counts=prefs["playlist_counts"],
                      related_ids=prefs["related_ids"],
                      preset=db.get_wave_preset())
              if mode == "balanced" else wave(pool, **prefs))
    if not result and pool is not candidates:
        # The only non-recent candidates might all be explicitly excluded.
        result = (
            wave_v2(candidates, prefs["liked_ids"], prefs["disliked_ids"],
                    ratings=prefs["ratings"], playback=prefs["playback"],
                    recent_ids=prefs["recent_ids"], count=count,
                    exposure=prefs["exposure"],
                    playlist_counts=prefs["playlist_counts"],
                    related_ids=prefs["related_ids"],
                    preset=db.get_wave_preset())
            if mode == "balanced" else wave(candidates, **prefs)
        )
    return result
