"""Fuse multiple ListenBrainz artist-radio rankings without extra user steps.

This is rank aggregation over *metadata* recommendations. No audio is
downloaded, played, or silently converted into a Telegram file_id.
"""
from __future__ import annotations

from collections import Counter
from typing import Sequence

from music_bot.catalog import CatalogTrack, normalized_identity


def fuse_artist_radio(
    ranked_pools: Sequence[Sequence[CatalogTrack]],
    *, limit: int = 15, k: int = 60,
) -> list[CatalogTrack]:
    """Reciprocal-rank fusion with a soft artist-diversity reranker.

    Agreement across independent positive seed radios earns higher rank.
    Candidate identity is normalized (artist, title); variants have already
    been rejected by the caller. Source ordering remains a deterministic
    tie-breaker. The strength of diversification never removes candidates.
    """
    if not 1 <= limit <= 50 or not 1 <= k <= 1000:
        raise ValueError("Invalid fusion arguments")
    score: dict[tuple[str, str], float] = {}
    first_seen: dict[tuple[str, str], int] = {}
    candidates: dict[tuple[str, str], CatalogTrack] = {}

    for pool in ranked_pools:
        within_pool: set[tuple[str, str]] = set()
        for rank, song in enumerate(pool, start=1):
            key = normalized_identity(song.title, song.artist)
            if key in within_pool:
                continue
            within_pool.add(key)
            if key not in candidates:
                candidates[key] = song
                first_seen[key] = len(first_seen)
            score[key] = score.get(key, 0.0) + 1.0 / (k + rank)

    remaining = set(candidates)
    result: list[CatalogTrack] = []
    seen_artists: Counter[str] = Counter()
    while remaining and len(result) < limit:
        best = max(
            remaining,
            key=lambda key: (
                score[key] / (1.0 + 0.45 * seen_artists[key[1]]),
                -first_seen[key],
            ),
        )
        remaining.remove(best)
        song = candidates[best]
        result.append(song)
        seen_artists[best[1]] += 1
    return result
