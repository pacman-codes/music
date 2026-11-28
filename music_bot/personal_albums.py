"""Bounded album recommendations from explicit taste and related artists.

Release-group metadata is not evidence that an album is playable.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import asdict, dataclass

from music_bot.albums import cached_album_group
from music_bot.artists import Artist, ReleaseGroup, artist_discography, search_artists
from music_bot.catalog import CatalogError, MusicCatalog, normalize, valid_mbid
from music_bot.personalized import ranked_seed_artists, strongly_negative_artists
from music_bot.storage import Library


@dataclass(frozen=True)
class AlbumRecommendation:
    mbid: str
    title: str
    artist: str
    date: str
    kind: str
    reason: str
    seed: str

    def public(self):
        return {**asdict(self), "playable": False, "cover_urls": [
            f"https://coverartarchive.org/release-group/{self.mbid}/front-250"]}


async def personal_albums(db: Library, catalog: MusicCatalog, *, page: int = 0,
                          limit: int = 8) -> dict:
    if not 0 <= page <= 4 or not 1 <= limit <= 12:
        raise ValueError("Invalid recommendation page")
    warnings = []
    seeds: list[Artist] = []
    used_ids = set()
    used_names = set()
    def add_seed(artist):
        if artist.mbid not in used_ids and len(seeds) < 2:
            seeds.append(artist)
            used_ids.add(artist.mbid)
            used_names.add(normalize(artist.name))

    # Explicit favorite IDs take priority over potentially ambiguous local tags.
    for row in db.saved_artists(limit=2):
        add_seed(Artist(row["mbid"], row["name"], row["disambiguation"]))
    for seed in ranked_seed_artists(db, count=3):
        if len(seeds) >= 2:
            break
        if normalize(seed.name) in used_names or not 2 <= len(seed.name) <= 90:
            continue
        try:
            candidates = await asyncio.wait_for(search_artists(catalog, seed.name), timeout=12)
        except (CatalogError, TimeoutError):
            warnings.append("Часть исполнителей не удалось определить")
            continue
        exact = [a for a in candidates if normalize(a.name) == normalize(seed.name)]
        # Never guess among same-name artists.
        if len(exact) == 1:
            add_seed(exact[0])

    if not seeds:
        return {"results": [], "seed_artists": [], "page": page, "has_more": False,
                "warnings": list(dict.fromkeys(warnings))}

    own = db.all_tracks(10000)
    excluded = strongly_negative_artists(
        own, db.liked_ids(), db.disliked_ids(), db.ratings(), db.playback_stats(),
        verdicts=db.track_verdicts())
    related: list[tuple[Artist, str]] = []
    for seed in seeds:
        try:
            found = await asyncio.wait_for(catalog.artist_radio_by_id(
                seed.mbid, difficulty="medium", limit=20), timeout=12)
        except (CatalogError, TimeoutError):
            warnings.append("Часть похожих исполнителей недоступна")
            continue
        for track in found:
            ident = track.artist_mbid
            name = track.artist.strip()
            key = normalize(name)
            if (not valid_mbid(ident) or not key or key in excluded or key in used_names
                    or ident.lower() in used_ids or track.variant):
                continue
            # One related artist per seed keeps the feed diverse.
            used_ids.add(ident.lower())
            used_names.add(key)
            related.append((Artist(ident.lower(), name), seed.name))
            break

    # Compare both group IDs (when cached) and normalized album names.
    saved_ids, saved_names = set(), set()
    offset = 0
    while True:
        batch = db.saved_albums(offset=offset, limit=100)
        for row in batch:
            saved_ids.add(row["mbid"])
            group = cached_album_group(catalog, row["mbid"])
            if group:
                saved_ids.add(group.lower())
            saved_names.add((normalize(row["artist"]), normalize(row["title"])))
        if len(batch) < 100:
            break
        offset += 100

    pools = []
    more = False
    successes = 0
    for artist, reason, seed_name in [
        *((a, "preferred", a.name) for a in seeds),
        *((a, "related", seed) for a, seed in related),
    ]:
        try:
            groups, has_more = await asyncio.wait_for(
                artist_discography(catalog, artist.mbid, page), timeout=12)
        except (CatalogError, TimeoutError):
            warnings.append("Часть дискографий недоступна")
            continue
        successes += 1
        more |= has_more
        pool = []
        for g in groups:
            if g.mbid in saved_ids or (normalize(artist.name), normalize(g.title)) in saved_names:
                continue
            pool.append(AlbumRecommendation(g.mbid, g.title, artist.name, g.date,
                                            g.kind, reason, seed_name))
        pools.append(pool)

    if not successes:
        raise CatalogError("Дискографии временно недоступны")

    # Round-robin across artists; no invented album popularity or audio scores.
    result, seen = [], set()
    for i in range(max((len(pool) for pool in pools), default=0)):
        for pool in pools:
            if i >= len(pool):
                continue
            candidate = pool[i]
            if candidate.mbid in seen:
                continue
            seen.add(candidate.mbid)
            result.append(candidate.public())
            if len(result) >= limit:
                break
        if len(result) >= limit:
            break
    return {"results": result, "seed_artists": [a.name for a in seeds],
            "page": page, "has_more": more and page < 4,
            "warnings": list(dict.fromkeys(warnings))}


async def recommend_albums(db: Library, catalog: MusicCatalog, *, page: int = 0) -> dict:
    """Shared lease prevents repeated clicks in chat and Mini App from piling up."""
    lease = db.acquire_media_import("taste", "personal-albums", ttl=180)
    if lease is None:
        raise CatalogError("Уже подбираю альбомы")
    try:
        return await personal_albums(db, catalog, page=page)
    finally:
        db.release_media_import("taste", "personal-albums", lease)
