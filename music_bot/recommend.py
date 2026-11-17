"""Rank *playable* tracks with explicit preferences and passive context.

Delivery history only measures Telegram sends, never actual listening.
No need for questionnaires, forced ratings or telemetry Telegram does not expose.
"""
from __future__ import annotations

import random
from collections import Counter


def fatigue_suppressed(skips: int, completions: int) -> bool:
    """Frequent manually reported skips suppress radio, not saved favorites."""
    return skips >= 3 and skips >= 2 * completions + 3


def track_weight(
    track, liked_ids, favorite_artists, seen_artist,
    *, recent_ids=frozenset(), ratings=None, playback=None, mode="balanced",
    exposure=None, playlist_counts=None, related_ids=frozenset(),
    recent_artists=(), maybe_ids=frozenset(),
) -> float:
    """Positive sampling weight with a mild, reversible exposure penalty.

    A custom playlist is a weak positive signal. A delivered song is not a
    disliked or skipped song; exposures only help rotate an overused library.
    """
    ratings = ratings or {}
    playback = playback or {}
    exposure = exposure or {}
    playlist_counts = playlist_counts or {}
    artist = track.artist.strip().casefold()
    liked = track.id in liked_ids
    score = 1.0
    if mode != "discover" and liked:
        score += 3.0
    if track.id in maybe_ids and not liked:
        # Interest is weaker than an actual favorite, not a fake play.
        score += 0.30
    if not liked and artist and artist in favorite_artists:
        score += 2.0

    rating = ratings.get(track.id)
    if rating is not None:
        score *= 0.25 + (rating - 1) * (1.55 / 9)

    skips, completions = playback.get(track.id, (0, 0))
    adjusted_skips = max(0, skips - 1)
    skip_penalty = 1 / (1 + 1.7 * (adjusted_skips ** 1.8) / (1 + .7 * completions))
    completion_bonus = 1 + min(0.75, completions * 0.12)
    score *= skip_penalty * completion_bonus

    # Strength is capped: putting a track in many playlists is not equivalent
    # to many confirmed plays, and must not swamp explicit ratings.
    score *= 1 + min(0.30, 0.08 * playlist_counts.get(track.id, 0))
    if track.id in related_ids:
        score *= 1.15

    if track.id in recent_ids:
        score *= 0.45
    score /= 1 + 0.65 * max(0.0, exposure.get(track.id, 0.0))

    if artist:
        score /= 1 + 2.5 * seen_artist.get(artist,0)
        # Diversify neighboring songs without hard bans on small libraries.
        if recent_artists and artist == recent_artists[-1]:
            score *= 0.12
        elif artist in recent_artists[-2:]:
            score *= 0.50
    return max(0.0001, score)


def wave(tracks, liked_ids, disliked_ids=frozenset(), count=30, seed=None,
         recent_ids=frozenset(), mode="balanced", *, ratings=None, playback=None,
         exposure=None, playlist_counts=None, related_ids=frozenset(),
         maybe_ids=frozenset()):
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
    recent_artists: list[str] = []
    out = []
    while pool and len(out) < count:
        weights = [
            track_weight(
                t, liked_ids, favorites, seen_artist,
                recent_ids=recent_ids, ratings=ratings, playback=playback, mode=mode,
                exposure=exposure, playlist_counts=playlist_counts,
                related_ids=related_ids, recent_artists=recent_artists,
                maybe_ids=maybe_ids,
            )
            for t in pool
        ]
        selected = rng.choices(range(len(pool)), weights=weights, k=1)[0]
        track = pool.pop(selected)
        artist = track.artist.strip().casefold()
        seen_artist[artist] += 1
        recent_artists.append(artist)
        out.append(track)
    return out
