"""Rank tracks using explicit 1–10 ratings and *reported* playback feedback.

Telegram's native player does not emit skip or complete events to bots.
All behavioral observations therefore originate in manual controls until an
instrumented, separately authenticated player exists.
"""
from __future__ import annotations

import random
from collections import Counter


def fatigue_suppressed(skips: int, completions: int) -> bool:
    """Frequent recent skips temporarily keep a song out of radio, not favorites.

    Counts reflect at most the last 20 explicit events over 30 days.
    A successful listening streak can restore eligibility; expiry also clears it.
    """
    return skips >= 3 and skips >= 2 * completions + 3


def track_weight(
    track, liked_ids, favorite_artists, seen_artist,
    *, recent_ids=frozenset(), ratings=None, playback=None, mode="balanced",
) -> float:
    """Positive sampling weight. Dislikes are filtered before calling this."""
    ratings = ratings or {}
    playback = playback or {}
    artist = track.artist.strip().casefold()
    liked = track.id in liked_ids
    score = 1.0
    if mode != "discover" and liked:
        score += 3.0
    if not liked and artist and artist in favorite_artists:
        score += 2.0

    # A rating is an opinion, *not* a dislike or a request to remove favorites.
    rating = ratings.get(track.id)
    if rating is not None:
        # 1 => 0.25x, 5 => 0.95x, 10 => 1.8x.
        score *= 0.25 + (rating - 1) * (1.55 / 9)

    skips, completions = playback.get(track.id, (0, 0))
    # One skip alone is not enough to punish a favorite; repeats matter.
    adjusted_skips = max(0, skips - 1)
    skip_penalty = 1 / (1 + 1.7 * (adjusted_skips ** 1.8) / (1 + .7 * completions))
    completion_bonus = 1 + min(0.75, completions * 0.12)
    score *= skip_penalty * completion_bonus
    if track.id in recent_ids:
        score *= 0.08
    if artist:
        score /= 1 + 3 * seen_artist[artist]
    return max(0.0001, score)


def wave(tracks, liked_ids, disliked_ids=frozenset(), count=30, seed=None,
         recent_ids=frozenset(), mode="balanced", *, ratings=None, playback=None):
    if mode not in {"balanced", "discover", "favorites"}:
        raise ValueError("Unknown wave mode")
    ratings = ratings or {}
    playback = playback or {}
    pool = [
        t for t in {item.id: item for item in tracks}.values()
        if t.id not in disliked_ids
        and not fatigue_suppressed(*playback.get(t.id, (0, 0)))
    ]
    if count <= 0 or not pool:
        return []
    rng = random.Random(seed)
    favorites = Counter(
        t.artist.strip().casefold() for t in pool
        if t.id in liked_ids and t.artist.strip()
    )
    if mode == "favorites":
        pool = [t for t in pool if t.id in liked_ids] or pool
    if mode == "discover" and any(t.id not in liked_ids for t in pool):
        pool = [t for t in pool if t.id not in liked_ids]
    seen_artist = Counter()
    out = []
    while pool and len(out) < count:
        weights = [
            track_weight(
                t, liked_ids, favorites, seen_artist,
                recent_ids=recent_ids, ratings=ratings, playback=playback, mode=mode,
            )
            for t in pool
        ]
        selected = rng.choices(range(len(pool)), weights=weights, k=1)[0]
        track = pool.pop(selected)
        seen_artist[track.artist.strip().casefold()] += 1
        out.append(track)
    return out
