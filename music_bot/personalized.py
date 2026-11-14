"""Multi-seed personal discovery from *reported* preferences.

ListenBrainz returns recording metadata, not audio. This module never claims
a recommendation can be played until an independently uploaded Telegram file
exists. The default is up to 3 seed artists and 20 candidate records.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from music_bot.catalog import (
    CatalogError, CatalogTrack, MusicCatalog, normalize, normalized_identity,
)
from music_bot.recommend import fatigue_suppressed
from music_bot.storage import Library


@dataclass(frozen=True)
class ArtistSeed:
    name: str
    score: float


def ranked_seed_artists(db: Library, count: int = 3) -> list[ArtistSeed]:
    """Choose up to three diverse artists using explicit ratings and favorites.

    A low-rated or frequently skipped *favorite* remains in "Мне нравится",
    but should not automatically become a positive seed for new discoveries.
    """
    if not 1 <= count <= 5:
        raise ValueError("Seed count must be between 1 and 5")
    likes = db.liked_ids()
    ratings = db.ratings()
    dislikes = db.disliked_ids()
    playback = db.playback_stats()
    artist_scores: dict[str, float] = defaultdict(float)
    representative: dict[str, str] = {}
    for t in db.all_tracks(1000):
        if t.id in dislikes:
            continue
        artist = t.artist.strip()
        key = normalize(artist)
        if not key or key in {"неизвестный исполнитель", "unknown artist"}:
            continue
        rate = ratings.get(t.id)
        if rate is not None and rate <= 4:
            continue
        if rate is None and t.id not in likes:
            continue
        skips, completes = playback.get(t.id, (0, 0))
        if fatigue_suppressed(skips, completes):
            continue
        weight = (2.0 if t.id in likes else 0.0)
        if rate is not None:
            weight += (rate - 5) * 1.5
        weight += min(2.0, completes * 0.30)
        weight -= min(2.0, skips * 0.50)
        if weight <= 0:
            continue
        representative.setdefault(key, artist)
        artist_scores[key] += weight
    return [
        ArtistSeed(representative[key], round(weight, 2))
        for key, weight in sorted(
            artist_scores.items(), key=lambda item: (-item[1], item[0])
        )[:count]
    ]


async def personalized_discovery(
    db: Library, catalog: MusicCatalog, *, difficulty: str = "medium",
    limit: int = 15, max_seeds: int = 3,
) -> tuple[list[str], list[CatalogTrack]]:
    """Collect LB radio from top-rated artist seeds, fairly interleaved.

    Partial catalog outages may reduce choices without breaking the entire
    feed. On total outage raise CatalogError. No user token is required.
    """
    if difficulty not in {"easy", "medium", "hard"}:
        raise ValueError("Unknown discovery difficulty")
    if not 1 <= limit <= 20 or not 1 <= max_seeds <= 3:
        raise ValueError("Invalid discovery size")
    seeds = ranked_seed_artists(db, count=max_seeds)
    if not seeds:
        return [], []

    known_ids = db.confirmed_recording_ids()
    own_tracks = db.all_tracks(1000)
    own_names = {normalized_identity(t.title, t.artist) for t in own_tracks}
    suppressed_names = {
        normalized_identity(t.title, t.artist)
        for t in own_tracks if t.id in db.disliked_ids()
        or fatigue_suppressed(*db.playback_stats().get(t.id, (0, 0)))
    }
    pools: list[list[CatalogTrack]] = []
    used_seeds: list[str] = []
    errors = 0
    for seed in seeds:
        try:
            found = await catalog.artist_radio(
                seed.name, difficulty=difficulty,
                limit=min(20, max(8, limit)),
            )
        except CatalogError:
            errors += 1
            continue
        used_seeds.append(seed.name)
        clean = [
            song for song in found
            if song.mbid not in known_ids and not song.variant
            and normalized_identity(song.title, song.artist) not in own_names
            and normalized_identity(song.title, song.artist) not in suppressed_names
        ]
        pools.append(clean)

    if errors == len(seeds):
        raise CatalogError("Не удалось связаться с музыкальными каталогами")

    result: list[CatalogTrack] = []
    seen_names: set[tuple[str, str]] = set()
    seen_ids: set[str] = set()
    cursor = 0
    # Per-seed round robin, no permanently assigned artist bias.
    while len(result) < limit and any(pools):
        pool = pools[cursor % len(pools)]
        cursor += 1
        if not pool:
            continue
        song = pool.pop(0)
        identity = normalized_identity(song.title, song.artist)
        if song.mbid in seen_ids or identity in seen_names:
            continue
        seen_ids.add(song.mbid)
        seen_names.add(identity)
        result.append(song)
    return used_seeds, result
