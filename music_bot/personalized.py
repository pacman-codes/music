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
from music_bot.discovery_ranking import fuse_artist_radio
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
    tracks = db.all_tracks(10000)
    for t in tracks:
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
    if not artist_scores:
        # Cold start: uploading/saving a file is weak evidence of interest,
        # never a fabricated listen or an explicit "like". Only use this
        # fallback when no strong positive ratings/favorites are available.
        excluded = strongly_negative_artists(
            tracks, likes, dislikes, ratings, playback
        )
        weak_counts: dict[str, int] = defaultdict(int)
        for track in tracks:
            key = normalize(track.artist.strip())
            if (not key or key in {"неизвестный исполнитель", "unknown artist"}
                    or key in excluded or track.id in dislikes
                    or ratings.get(track.id, 10) <= 4
                    or fatigue_suppressed(*playback.get(track.id, (0, 0)))):
                continue
            weak_counts[key] += 1
            representative.setdefault(key, track.artist.strip())
        # Limit the preference inferred from a large imported discography.
        artist_scores = {
            key: min(5, occurrences) * 0.10
            for key, occurrences in weak_counts.items()
        }
    return [
        ArtistSeed(representative[key], round(weight, 2))
        for key, weight in sorted(
            artist_scores.items(), key=lambda item: (-item[1], item[0])
        )[:count]
    ]


def strongly_negative_artists(tracks, liked_ids, disliked_ids, ratings, playback):
    """Conservatively exclude an artist only with repeated explicit negatives.

    One disliked song does not mean the whole artist is disliked. Any clear
    positive rating/favorite for that artist prevents the artist-level ban.
    """
    negative: dict[str, set[int]] = defaultdict(set)
    positive: set[str] = set()
    for track in tracks:
        artist = normalize(track.artist.strip())
        if not artist:
            continue
        rating = ratings.get(track.id)
        if rating is not None and rating >= 8:
            positive.add(artist)
        elif track.id in liked_ids and (rating is None or rating >= 5):
            positive.add(artist)
        if (track.id in disliked_ids or
                (rating is not None and rating <= 3) or
                fatigue_suppressed(*playback.get(track.id, (0, 0)))):
            negative[artist].add(track.id)
    return {
        artist for artist, song_ids in negative.items()
        if len(song_ids) >= 2 and artist not in positive
    }


async def personalized_discovery(
    db: Library, catalog: MusicCatalog, *, difficulty: str = "medium",
    limit: int = 15, max_seeds: int = 3,
) -> tuple[list[str], list[CatalogTrack]]:
    """Collect LB radio from top-rated artist seeds, rank-fused and diversified.

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
    decided_mbids = set(db.catalog_verdicts())
    own_tracks = db.all_tracks(10000)
    own_names = {normalized_identity(t.title, t.artist) for t in own_tracks}
    likes, dislikes = db.liked_ids(), db.disliked_ids()
    ratings, feedback = db.ratings(), db.playback_stats()
    suppressed_names = {
        normalized_identity(t.title, t.artist)
        for t in own_tracks if t.id in dislikes
        or fatigue_suppressed(*feedback.get(t.id, (0, 0)))
    }
    excluded_artists = strongly_negative_artists(
        own_tracks, likes, dislikes, ratings, feedback
    )
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
            if song.mbid not in known_ids and song.mbid not in decided_mbids
            and not song.variant
            and normalized_identity(song.title, song.artist) not in own_names
            and normalized_identity(song.title, song.artist) not in suppressed_names
            and normalize(song.artist) not in excluded_artists
        ]
        pools.append(clean)

    if errors == len(seeds):
        raise CatalogError("Не удалось связаться с музыкальными каталогами")

    # An item suggested by multiple different preferred artists is usually
    # a stronger bridge than a recommendation from only one seed. Reciprocal
    # rank fusion joins their rankings, then softens artist repetition.
    return used_seeds, fuse_artist_radio(pools, limit=limit)
