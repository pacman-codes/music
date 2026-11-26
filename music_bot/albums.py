"""Album metadata and exact release matching, never a source of audio files."""
from __future__ import annotations

import re
from dataclasses import dataclass

from music_bot.catalog import (
    CatalogError, CatalogTrack, MusicCatalog, _get_artist_credit, is_variant,
    normalize, valid_mbid,
)


def words(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\w+", normalize(value)))


def album_query(text: str) -> tuple[str, bool]:
    # Support natural messages, not a required slash command.
    parts = text.strip().split()
    markers = {"альбом", "эп", "album", "ep"}
    indices = [i for i,p in enumerate(parts) if p.casefold().strip(":") in markers]
    if not indices:
        return " ".join(parts), False
    # Only remove one marker; an album title itself may include "album".
    index = next((i for i in indices if parts[i].casefold().strip(":") in {"альбом", "эп"}), indices[0])
    return " ".join(parts[:index] + parts[index+1:]), True


@dataclass(frozen=True)
class Album:
    mbid: str
    title: str
    artist: str
    date: str
    track_count: int
    group: str


def parse_album(item) -> Album | None:
    if not isinstance(item, dict) or not valid_mbid(item.get("id")):
        return None
    title = item.get("title")
    artist, _ = _get_artist_credit(item.get("artist-credit"))
    group = item.get("release-group")
    if not isinstance(group, dict):
        return None
    if group.get("primary-type") not in {"Album", "EP"}:
        return None
    if item.get("status") not in (None, "Official"):
        return None
    count = item.get("track-count")
    if type(count) is not int or not 2 <= count <= 100:
        return None
    if not isinstance(title, str) or not title.strip() or not artist:
        return None
    date = item.get("date")
    return Album(item["id"].lower(), title.strip()[:180], artist[:180],
                 date[:10] if isinstance(date, str) else "", count,
                 group["id"].lower() if valid_mbid(group.get("id")) else item["id"].lower())


def release_search_expression(text: str, *, allow_title_only: bool) -> str:
    """Search artist and release fields together, not a bare title search.

    Try both artist/title orders without guessing where a multiword name ends.
    Keep the number of Lucene clauses bounded by the 90-character input limit.
    """
    parts = text.split()
    def quote(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    clauses = []
    for split in range(1, len(parts)):
        left, right = quote(" ".join(parts[:split])), quote(" ".join(parts[split:]))
        clauses.extend((f"(artist:{left} AND release:{right})",
                        f"(artist:{right} AND release:{left})"))
    if allow_title_only:
        clauses.append(f"release:{quote(text)}")
    if not clauses:
        return f"release:{quote(text)}"
    return " OR ".join(clauses)


async def find_albums(catalog: MusicCatalog, text: str, *, exact: bool) -> list[Album]:
    text = " ".join(text.split())
    if not 2 <= len(text) <= 90:
        raise ValueError("Invalid album query")
    wanted = words(text)
    if not wanted:
        return []
    # With an explicit album request, permit a single-word title search.
    # Multiword requests require both the artist and the full release title;
    # never silently drop an artist supplied by the owner.
    title_only = not exact and len(wanted) == 1
    if exact and len(wanted) == 1:
        return []
    expression = release_search_expression(text, allow_title_only=title_only)
    payload = await catalog._json("mb", "/ws/2/release/",
                                  {"query": expression, "fmt": "json", "limit": 50},
                                  ttl=3600)
    if not isinstance(payload, dict) or not isinstance(payload.get("releases"), list):
        raise CatalogError("Некорректный ответ каталога альбомов")
    seen = set()
    results = []
    for raw in payload["releases"]:
        album = parse_album(raw)
        if album is None or album.group in seen:
            continue
        a, b = words(album.artist), words(album.title)
        if wanted not in {a+b, b+a} and not (title_only and wanted == b):
            continue
        seen.add(album.group)
        results.append(album)
        if len(results) == 4:
            break
    return results


async def album_tracks(catalog: MusicCatalog, mbid: str) -> tuple[str, str, list[CatalogTrack]]:
    if not valid_mbid(mbid):
        raise ValueError("Invalid release MBID")
    payload = await catalog._json("mb", "/ws/2/release/" + mbid.lower(),
                                  {"fmt": "json", "inc": "recordings+artist-credits+release-groups"},
                                  ttl=7*86400)
    if not isinstance(payload, dict) or not isinstance(payload.get("media"), list):
        raise CatalogError("Не удалось получить трек-лист")
    title = payload.get("title")
    artist, _ = _get_artist_credit(payload.get("artist-credit"))
    if not isinstance(title, str) or not title.strip() or not artist:
        raise CatalogError("Не удалось определить альбом")
    result = []
    for medium in payload["media"]:
        if not isinstance(medium, dict) or not isinstance(medium.get("tracks"), list):
            raise CatalogError("Неполный трек-лист")
        for item in medium["tracks"]:
            if not isinstance(item, dict) or not isinstance(item.get("recording"), dict):
                raise CatalogError("Неполный трек-лист")
            recording = item["recording"]
            name = item.get("title") or recording.get("title")
            credit, artist_id = _get_artist_credit(item.get("artist-credit") or recording.get("artist-credit"))
            if not credit:
                credit, artist_id = _get_artist_credit(payload.get("artist-credit"))
            if not valid_mbid(recording.get("id")) or not isinstance(name, str) or not name.strip():
                raise CatalogError("Неполный трек-лист")
            duration = item.get("length") or recording.get("length")
            if type(duration) is not int or not 0 < duration <= 14_400_000:
                duration = None
            result.append(CatalogTrack(recording["id"].lower(), name[:250], credit[:250],
                                       artist_id, duration, is_variant(name)))
            if len(result) > 100:
                raise CatalogError("Альбом содержит больше 100 треков")
    if not result:
        raise CatalogError("Трек-лист пуст")
    return title[:180], artist[:180], result
