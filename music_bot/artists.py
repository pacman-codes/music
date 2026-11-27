"""MusicBrainz artist profiles and paginated album/EP discographies.

Catalog metadata is separate from locally playable audio.
"""
from __future__ import annotations

from dataclasses import dataclass

from music_bot.albums import Album, parse_album
from music_bot.catalog import CatalogError, MusicCatalog, normalize, valid_mbid


@dataclass(frozen=True)
class Artist:
    mbid: str
    name: str
    disambiguation: str = ""


@dataclass(frozen=True)
class ReleaseGroup:
    mbid: str
    title: str
    date: str
    kind: str


def parse_artist(row) -> Artist | None:
    if not isinstance(row, dict) or not valid_mbid(row.get("id")):
        return None
    name = row.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    description = row.get("disambiguation")
    return Artist(row["id"].lower(), name.strip()[:180],
                  description[:180] if isinstance(description, str) else "")


async def search_artists(catalog: MusicCatalog, query: str) -> list[Artist]:
    query = " ".join(query.split())
    if not 2 <= len(query) <= 90:
        raise ValueError("Invalid artist query")
    escaped = query.replace("\\", "\\\\").replace('"', '\\"')
    data = await catalog._json("mb", "/ws/2/artist/",
                               {"query": f'artist:"{escaped}"', "fmt": "json", "limit": 20},
                               ttl=86400)
    if not isinstance(data, dict) or not isinstance(data.get("artists"), list):
        raise CatalogError("Некорректный ответ каталога исполнителей")
    results = {}
    for raw in data["artists"]:
        artist = parse_artist(raw)
        if artist:
            results.setdefault(artist.mbid, artist)
    return sorted(results.values(), key=lambda artist: normalize(artist.name) != normalize(query))[:8]


async def artist_by_id(catalog: MusicCatalog, mbid: str) -> Artist:
    if not valid_mbid(mbid):
        raise ValueError("Invalid artist ID")
    data = await catalog._json("mb", "/ws/2/artist/" + mbid.lower(), {"fmt": "json"}, ttl=7*86400)
    artist = parse_artist(data)
    if artist is None or artist.mbid != mbid.lower():
        raise CatalogError("Исполнитель недоступен")
    return artist


async def artist_discography(catalog: MusicCatalog, mbid: str, page: int = 0):
    """Use release groups to avoid duplicate editions; one upstream page at a time."""
    if not valid_mbid(mbid) or not 0 <= page <= 999:
        raise ValueError("Invalid artist discography")
    size = 10
    data = await catalog._json("mb", "/ws/2/release-group/",
                               {"query": f"arid:{mbid.lower()} AND (primarytype:album OR primarytype:ep)",
                                "fmt": "json", "limit": size, "offset": page*size},
                               ttl=7*86400)
    if not isinstance(data, dict) or not isinstance(data.get("release-groups"), list):
        raise CatalogError("Дискография недоступна")
    result = []
    seen = set()
    for item in data["release-groups"][:size]:
        if not isinstance(item, dict) or not valid_mbid(item.get("id")):
            continue
        kind, title = item.get("primary-type"), item.get("title")
        credits = item.get("artist-credit")
        if not isinstance(credits, list) or not any(
                isinstance(c, dict) and isinstance(c.get("artist"), dict)
                and c["artist"].get("id") == mbid.lower() for c in credits):
            continue
        if kind not in {"Album", "EP"} or not isinstance(title, str) or not title.strip():
            continue
        # Exclude compilations, live collections and other secondary release types.
        if item.get("secondary-types"):
            continue
        ident = item["id"].lower()
        if ident in seen:
            continue
        seen.add(ident)
        date = item.get("first-release-date")
        result.append(ReleaseGroup(ident, title[:180], date[:10] if isinstance(date, str) else "", kind))
    count = data.get("count")
    has_more = page < 999 and (type(count) is int and (page+1)*size < count
                               or type(count) is not int and len(data["release-groups"]) == size)
    return result, has_more


async def group_album(catalog: MusicCatalog, mbid: str) -> Album:
    """Resolve an official edition from a release group, never by title similarity."""
    if not valid_mbid(mbid):
        raise ValueError("Invalid release group")
    data = await catalog._json("mb", "/ws/2/release/",
                               {"query": f"rgid:{mbid.lower()} AND status:official",
                                "fmt": "json", "limit": 50}, ttl=86400)
    if not isinstance(data, dict) or not isinstance(data.get("releases"), list):
        raise CatalogError("Издание недоступно")
    choices = []
    for raw in data["releases"]:
        album = parse_album(raw)
        if album and album.group == mbid.lower() and raw.get("status") == "Official":
            choices.append(album)
    if not choices:
        raise CatalogError("Нет подходящего официального издания с 2–100 треками")
    return min(choices, key=lambda a: (not bool(a.date), a.date, a.mbid))
