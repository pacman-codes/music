"""Offline recommender for saved audio only.

Future external candidate providers are independent from this ranking function.
"""
from __future__ import annotations

import random
from collections import Counter


def wave(tracks, liked_ids, disliked_ids=frozenset(), count=30, seed=None,
         recent_ids=frozenset(), mode="balanced"):
    if mode not in {"balanced", "discover", "favorites"}:
        raise ValueError("Unknown wave mode")
    pool = list({t.id: t for t in tracks if t.id not in disliked_ids}.values())
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
        weights = []
        for t in pool:
            artist = t.artist.strip().casefold()
            is_liked = t.id in liked_ids
            score = 1.0
            if mode != "discover" and is_liked:
                score += 3.0
            if not is_liked and artist and artist in favorites:
                score += 2.0
            if t.id in recent_ids:
                score *= 0.08
            if artist:
                score /= 1 + 3 * seen_artist[artist]
            weights.append(max(0.0001, score))
        index = rng.choices(range(len(pool)), weights=weights, k=1)[0]
        track = pool.pop(index)
        seen_artist[track.artist.strip().casefold()] += 1
        out.append(track)
    return out
