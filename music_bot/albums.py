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


async def find_albums(catalog: MusicCatalog, text: str, *, exact: bool) -> list[Album]:
    if not 2 <= len(text.strip()) <= 90:
        raise ValueError("Invalid album query")
    escaped = re.sub(r'([+\-!(){}\[\]^"~*?:\\/])', r'\\\1', text.strip())
    payload = await catalog._json("mb", "/ws/2/release/",
                                  {"query": escaped, "fmt": "json", "limit": 25})
    if not isinstance(payload, dict) or not isinstance(payload.get("releases"), list):
        raise CatalogError("Некорректный ответ каталога альбомов")
    wanted = words(text)
    seen = set()
    results = []
    for raw in payload["releases"]:
        album = parse_album(raw)
        if album is None or album.group in seen:
            continue
        a, b = words(album.artist), words(album.title)
        if exact and wanted not in {a+b, b+a}:
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
