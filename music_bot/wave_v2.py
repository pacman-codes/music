"""Wave 2.0: quota-controlled, fully playable local music mix.

Familiar = loved or highly rated songs; Similar = other locally uploaded
songs by artists the owner explicitly prefers; New = remaining local songs.
External MusicBrainz/ListenBrainz candidates are metadata, NEVER streamable.
"""
from __future__ import annotations

import random
from collections import Counter

from music_bot.recommend import fatigue_suppressed, track_weight

PRESETS: dict[str, tuple[float, float, float]] = {
    "balanced": (0.60, 0.25, 0.15),
    "familiar": (0.80, 0.15, 0.05),
    "explore": (0.35, 0.30, 0.35),
}
CATEGORIES = ("familiar", "similar", "new")


def classify(tracks, liked_ids, ratings, playback, related_ids=frozenset()):
    """Return category buckets without turning a low rating into a dislike."""
    positive_artists = {
        t.artist.strip().casefold()
        for t in tracks
        if (t.id in liked_ids or ratings.get(t.id, 0) >= 8)
        and ratings.get(t.id, 10) >= 5
        and not fatigue_suppressed(*playback.get(t.id, (0, 0)))
        and t.artist.strip()
    }
    groups = {category: [] for category in CATEGORIES}
    for t in tracks:
        loved = t.id in liked_ids or ratings.get(t.id, 0) >= 8
        if loved:
            category = "familiar"
        elif (ratings.get(t.id, 10) >= 5 and
              (t.artist.strip().casefold() in positive_artists or t.id in related_ids)):
            category = "similar"
        else:
            category = "new"
        groups[category].append(t)
    return groups


def allocate_mix(size: int, capacities: dict[str, int], preset: str) -> dict[str, int]:
    """Largest-remainder quota allocation, with graceful category shortages."""
    if preset not in PRESETS:
        raise ValueError("Unknown Wave 2.0 preset")
    size = max(0, min(size, sum(capacities.get(k, 0) for k in CATEGORIES)))
    ratios = PRESETS[preset]
    raw = [size * ratio for ratio in ratios]
    counts = {
        category: min(capacities.get(category, 0), int(raw[i]))
        for i, category in enumerate(CATEGORIES)
    }
    while sum(counts.values()) < size:
        possible = [category for category in CATEGORIES
                    if counts[category] < capacities.get(category, 0)]
        if not possible:
            break
        # Pick the most under-represented category relative to the desired
        # quota, even when one original pool has already been exhausted.
        chosen = max(possible, key=lambda c: (
            raw[CATEGORIES.index(c)] - counts[c],
            ratios[CATEGORIES.index(c)],
            CATEGORIES.index(c) if preset == "explore" else -CATEGORIES.index(c),
        ))
        counts[chosen] += 1
    return counts


def wave_v2(tracks, liked_ids, disliked_ids=frozenset(), *, ratings=None,
            playback=None, recent_ids=frozenset(), count=10,
            preset="balanced", seed=None, exposure=None,
            playlist_counts=None, related_ids=frozenset(),
            maybe_ids=frozenset()):
    """Personalized native playback batch, no hypothetical audio candidates."""
    if preset not in PRESETS:
        raise ValueError("Unknown Wave 2.0 preset")
    if count <= 0:
        return []
    ratings = ratings or {}
    playback = playback or {}
    eligible = [
        track for track in {t.id: t for t in tracks}.values()
        if track.id not in disliked_ids
        and not fatigue_suppressed(*playback.get(track.id, (0, 0)))
    ]
    if not eligible:
        return []
    groups = classify(eligible, liked_ids, ratings, playback, related_ids)
    limits = allocate_mix(count, {key: len(value) for key, value in groups.items()}, preset)
    rng = random.Random(seed)
    # Interleave quota buckets without long same-category runs when possible.
    remaining = dict(limits)
    schedule = []
    while any(remaining.values()):
        possible = [name for name in CATEGORIES if remaining[name] > 0]
        alternative = [name for name in possible
                       if not schedule or name != schedule[-1]]
        if alternative:
            possible = alternative
        choice = rng.choices(possible, weights=[remaining[n] for n in possible], k=1)[0]
        schedule.append(choice)
        remaining[choice] -= 1
    favorites = Counter(t.artist.strip().casefold() for t in eligible
                        if t.id in liked_ids or ratings.get(t.id, 0) >= 8)
    seen_artists = Counter()
    recent_artists: list[str] = []
    result = []
    for name in schedule:
        pool = groups[name]
        scores = [
            track_weight(
                t, liked_ids, favorites, seen_artists,
                recent_ids=recent_ids, ratings=ratings, playback=playback,
                mode="balanced", exposure=exposure,
                playlist_counts=playlist_counts, related_ids=related_ids,
                recent_artists=recent_artists,
                maybe_ids=maybe_ids,
            )
            for t in pool
        ]
        index = rng.choices(range(len(pool)), weights=scores, k=1)[0]
        track = pool.pop(index)
        artist = track.artist.strip().casefold()
        seen_artists[artist] += 1
        recent_artists.append(artist)
        result.append(track)
    return result
