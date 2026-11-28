"""Generate each radio batch using the latest explicit taste and send history.

Radio queues are open-ended. Explicit playlists remain fixed.
No Telegram-native skip/completion events are available.
"""
from __future__ import annotations

from collections import defaultdict

from music_bot.recommend import artist_key, fatigue_suppressed, wave
from music_bot.wave_v2 import wave_v2
from music_bot.storage import Library, Track


DYNAMIC_MODES = frozenset(("balanced", "discover", "favorites"))


def wave_profile(db: Library, tracks, *, liked_ids, disliked_ids, ratings,
                 playback, suppressed, fresh_positive_ids):
    """Build artist preferences before applying queue filters.

    Imported likes and saved artist names are weak matching signals, not audio
    identity claims. Recent positives boost the current taste, with capped
    influence from large collections. Negative local feedback wins over weak
    imported artist affinity.
    """
    from music_bot.personalized import strongly_negative_artists
    from music_bot.catalog import normalize

    excluded = strongly_negative_artists(
        tracks, liked_ids, disliked_ids, ratings, playback,
        verdicts=db.track_verdicts())
    scores = defaultdict(float)
    preferred = set()
    for t in tracks:
        key = artist_key(t.artist)
        if (not key or key in {"unknown artist", "неизвестный исполнитель"}
                or t.id in suppressed or t.id in disliked_ids
                or ratings.get(t.id, 10) < 5
                or fatigue_suppressed(*playback.get(t.id, (0, 0)))):
            continue
        if t.id in liked_ids or ratings.get(t.id, 0) >= 8:
            preferred.add(key)
            scores[key] += 1.0 + (0.5 if t.id in fresh_positive_ids else 0)
    for row in db.saved_artists(limit=100):
        key = artist_key(row["name"])
        if key and normalize(row["name"]) not in excluded:
            preferred.add(key)
            scores[key] += 2.0
    for name, weight in db.taste_artist_scores():
        key = artist_key(name)
        if key and normalize(name) not in excluded:
            scores[key] += min(2.0, weight)
            if weight >= 1.0:
                preferred.add(key)
    for name, status in db.catalog_preference_artists():
        key = artist_key(name)
        if key and normalize(name) not in excluded:
            scores[key] += 1.0 if status == "add" else 0.15
            if status == "add":
                preferred.add(key)
    return preferred, {key:min(4.5, value) for key,value in scores.items()}


def select_wave_batch(db: Library, mode: str, count: int = 10,
                      *, queue_id: int | None = None) -> list[Track]:
    if mode not in DYNAMIC_MODES:
        raise ValueError("Unknown radio mode")
    if not 1 <= count <= 10:
        raise ValueError("Batch size must be 1–10")
    all_tracks = db.all_tracks(limit=10000)
    suppressed = db.suppressed_verdict_track_ids()
    liked_ids, disliked_ids = db.liked_ids(), db.disliked_ids()
    ratings, playback = db.ratings(), db.playback_stats()
    verdicts = db.track_verdicts()
    candidates = [t for t in all_tracks if t.id not in suppressed
                  and t.id not in disliked_ids
                  and not fatigue_suppressed(*playback.get(t.id, (0, 0)))]
    if queue_id is not None:
        filters = db.get_queue_filters(queue_id)
        if filters["liked_only"]:
            candidates = [t for t in candidates if t.id in liked_ids]
        if filters["min_rating"]:
            candidates = [t for t in candidates if ratings.get(t.id, 0) >= filters["min_rating"]]
        if filters["no_repeat_today"]:
            already_sent = db.sent_today_ids()
            candidates = [t for t in candidates if t.id not in already_sent]
    if not candidates:
        return []

    settings = db.get_wave_settings()
    context = db.wave_recent_context(settings["repeat_window"])
    preferred, affinity = wave_profile(
        db, all_tracks, liked_ids=liked_ids, disliked_ids=disliked_ids,
        ratings=ratings, playback=playback, suppressed=suppressed,
        fresh_positive_ids=context["fresh_positive_ids"])
    seeds = {
        tid for tid in (liked_ids | {tid for tid, value in ratings.items() if value >= 8})
        if tid not in disliked_ids and tid not in suppressed
        and ratings.get(tid, 10) >= 5
        and not fatigue_suppressed(*playback.get(tid, (0, 0)))
    }
    prefs = {
        "liked_ids": liked_ids,
        "disliked_ids": disliked_ids,
        "recent_ids": db.recent_ids(60),
        "ratings": ratings,
        "playback": playback,
        "maybe_ids": {tid for tid,status in verdicts.items() if status == "maybe"},
        **db.recommendation_signals(seeds),
        **context,
        "preferred_artists": preferred,
        "artist_affinity": affinity,
        "count": count,
        "diversity": settings["diversity"],
    }
    if mode == "balanced":
        return wave_v2(candidates, preset=db.get_wave_preset(), **prefs)
    return wave(candidates, mode=mode, **prefs)
