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


def artist_key(name: str) -> str:
    return " ".join(name.casefold().split())


def rotation_pool(pool, rotation_ids, immediate_ids, recent_artists, diversity=50, seen_artist=None):
    """Prefer unsent-in-recent-window tracks, then older repeats, then any.

    Within that tier, avoid adjacent artists when possible. A one-artist
    category or a tiny library never becomes unplayable.
    """
    fresh = [t for t in pool if t.id not in rotation_ids and t.id not in immediate_ids]
    older = [t for t in pool if t.id not in immediate_ids]
    eligible = fresh or older or pool
    if diversity >= 50 and recent_artists:
        window = recent_artists[-2:] if diversity >= 75 else recent_artists[-1:]
        alternative = [t for t in eligible if artist_key(t.artist) not in window]
        if not alternative and diversity >= 75:
            alternative = [t for t in eligible if artist_key(t.artist) != recent_artists[-1]]
        if alternative:
            eligible = alternative
    if diversity >= 75 and seen_artist is not None:
        least = min(seen_artist.get(artist_key(t.artist), 0) for t in eligible)
        eligible = [t for t in eligible if seen_artist.get(artist_key(t.artist), 0) == least]
    return eligible


def track_weight(
    track, liked_ids, favorite_artists, seen_artist,
    *, recent_ids=frozenset(), ratings=None, playback=None, mode="balanced",
    exposure=None, playlist_counts=None, related_ids=frozenset(),
    recent_artists=(), maybe_ids=frozenset(), artist_affinity=None,
    fresh_positive_ids=frozenset(), diversity=50,
) -> float:
    """Positive sampling weight with a mild, reversible exposure penalty.

    A custom playlist is a weak positive signal. A delivered song is not a
    disliked or skipped song; exposures only help rotate an overused library.
    """
    ratings = ratings or {}
    playback = playback or {}
    exposure = exposure or {}
    playlist_counts = playlist_counts or {}
    artist = artist_key(track.artist)
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
    # Recent explicit positive feedback nudges, rather than replaces, old taste.
    if track.id in fresh_positive_ids and (rating is None or rating >= 5):
        score *= 1.25
    if rating is None or rating >= 5:
        score *= 1 + min(0.45, 0.10 * (artist_affinity or {}).get(artist, 0.0))

    # Strength is capped: putting a track in many playlists is not equivalent
    # to many confirmed plays, and must not swamp explicit ratings.
    score *= 1 + min(0.30, 0.08 * playlist_counts.get(track.id, 0))
    if track.id in related_ids:
        score *= 1.15

    if track.id in recent_ids:
        score *= 0.45
    score /= 1 + 0.65 * max(0.0, exposure.get(track.id, 0.0))

    if artist:
        strength = diversity / 50
        score /= 1 + 2.5 * strength * seen_artist.get(artist,0)
        # Diversify neighboring songs without hard bans on small libraries.
        if recent_artists and artist == recent_artists[-1]:
            score *= 0.12 ** strength
        elif artist in recent_artists[-2:]:
            score *= 0.50 ** strength
    return max(0.0001, score)


def wave(tracks, liked_ids, disliked_ids=frozenset(), count=30, seed=None,
         recent_ids=frozenset(), mode="balanced", *, ratings=None, playback=None,
         exposure=None, playlist_counts=None, related_ids=frozenset(),
         maybe_ids=frozenset(), rotation_ids=frozenset(),
         immediate_ids=frozenset(), previous_artists=(),
         preferred_artists=frozenset(), artist_affinity=None,
         fresh_positive_ids=frozenset(), diversity=50):
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
        artist_key(t.artist) for t in pool
        if t.id in liked_ids and t.artist.strip()
    )
    favorites.update(preferred_artists)
    if mode == "favorites":
        pool = [t for t in pool if t.id in liked_ids] or pool
    if mode == "discover" and any(t.id not in liked_ids for t in pool):
        pool = [t for t in pool if t.id not in liked_ids]
    seen_artist = Counter()
    recent_artists = list(previous_artists)[-3:]
    out = []
    while pool and len(out) < count:
        choices = rotation_pool(pool, rotation_ids, immediate_ids, recent_artists, diversity, seen_artist)
        weights = [
            track_weight(
                t, liked_ids, favorites, seen_artist,
                recent_ids=recent_ids, ratings=ratings, playback=playback, mode=mode,
                exposure=exposure, playlist_counts=playlist_counts,
                related_ids=related_ids, recent_artists=recent_artists,
                maybe_ids=maybe_ids, artist_affinity=artist_affinity,
                fresh_positive_ids=fresh_positive_ids, diversity=diversity,
            )
            for t in choices
        ]
        track = rng.choices(choices, weights=weights, k=1)[0]
        pool.remove(track)
        artist = artist_key(track.artist)
        seen_artist[artist] += 1
        recent_artists.append(artist)
        out.append(track)
    return out
