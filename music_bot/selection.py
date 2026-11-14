"""Generate each radio batch using the latest owner ratings and feedback.

Radio queues are open-ended. Explicit playlists remain fixed.
No Telegram-native skip/completion events are available.
"""
from __future__ import annotations

from music_bot.recommend import wave
from music_bot.storage import Library, Track


DYNAMIC_MODES = frozenset(("balanced", "discover", "favorites"))


def select_wave_batch(db: Library, mode: str, count: int = 10) -> list[Track]:
    if mode not in DYNAMIC_MODES:
        raise ValueError("Unknown radio mode")
    if not 1 <= count <= 10:
        raise ValueError("Batch size must be 1–10")
    candidates = db.all_tracks()
    if not candidates:
        return []

    # Avoid an immediate replay of the most recently sent three songs,
    # but never dead-end a tiny personal library.
    no_repeat = db.latest_sent_ids(3)
    filtered = [t for t in candidates if t.id not in no_repeat]
    pool = filtered if len(filtered) >= min(count, 3) else candidates
    prefs = {
        "liked_ids": db.liked_ids(),
        "disliked_ids": db.disliked_ids(),
        "recent_ids": db.recent_ids(60),
        "ratings": db.ratings(),
        "playback": db.playback_stats(),
        "mode": mode,
        "count": count,
    }
    result = wave(pool, **prefs)
    if not result and pool is not candidates:
        # The only non-recent candidates might all be explicitly excluded.
        result = wave(candidates, **prefs)
    return result
