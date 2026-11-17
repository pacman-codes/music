"""Import *metadata-only* likes from public Yandex playlists and YouTube Music exports.

Never require Yandex/Google credentials, download audio, or manufacture listening
events. Public Yandex API is not a contract: fail closed on unexpected payloads.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urlsplit

import httpx

MAX_SOURCE_BYTES = 24 * 1024 * 1024
MAX_SOURCE_TRACKS = 10000
PLAYLIST_ID = re.compile(r"^(?:lk\.|pl\.)?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
VIDEO_ID = re.compile(r"^[a-zA-Z0-9_-]{11}$")
DURATION = re.compile(r"^(?:\d{1,3}:)?\d{1,2}:\d{2}$")


class TasteSourceError(ValueError):
    pass


@dataclass(frozen=True)
class SourceSong:
    external_id: str
    title: str
    artist: str
    url: str
    liked_at: str | None = None

    def __post_init__(self):
        if (not self.external_id or len(self.external_id) > 128
            or not self.title.strip() or len(self.title) > 350
            or not self.artist.strip() or len(self.artist) > 300
            or len(self.url) > 600 or self.liked_at and len(self.liked_at) > 50):
            raise TasteSourceError("Недопустимые метаданные трека")


def parse_yandex_url(url: str) -> str:
    """Extract only a UUID; URLs never become HTTP fetch targets."""
    try:
        parsed = urlsplit(url.strip())
        if (parsed.scheme != "https" or parsed.hostname not in
                {"music.yandex.ru", "music.yandex.com"} or parsed.port not in (None, 443)
                or parsed.username or parsed.password):
            raise TasteSourceError("Нужна HTTPS-ссылка на плейлист Яндекс Музыки")
        path = parsed.path.strip("/").split("/")
        if len(path) != 2 or path[0] != "playlists" or not PLAYLIST_ID.fullmatch(path[1]):
            raise TasteSourceError("Неверная ссылка на плейлист Яндекс Музыки")
        return path[1].lower()
    except (ValueError, TypeError) as exc:
        if isinstance(exc, TasteSourceError):
            raise
        raise TasteSourceError("Неверная ссылка Яндекс Музыки") from exc


def parse_yandex_playlist(payload: object) -> list[SourceSong]:
    if not isinstance(payload, dict):
        raise TasteSourceError("Яндекс вернул неожиданный формат")
    pl = payload.get("result", payload)
    if not isinstance(pl, dict):
        raise TasteSourceError("Яндекс вернул неожиданный плейлист")
    tracks = pl.get("tracks")
    if not isinstance(tracks, list) or not tracks or len(tracks) > MAX_SOURCE_TRACKS:
        raise TasteSourceError("Яндекс вернул пустой, неполный или слишком большой плейлист")
    count = pl.get("trackCount")
    # Deleted/unavailable entries can make the count differ, but a short
    # partial response must never silently erase an existing local snapshot.
    if type(count) is int and len(tracks) < max(1, count * .80):
        raise TasteSourceError("Яндекс прислал неполный список; старые данные сохранены")
    songs = []
    for item in tracks:
        if not isinstance(item, dict):
            continue
        track = item.get("track")
        if not isinstance(track, dict):
            continue
        title = track.get("title")
        artist_entries = track.get("artists")
        artists = ", ".join(
            a["name"].strip() for a in artist_entries
            if isinstance(a, dict) and isinstance(a.get("name"), str) and a["name"].strip()
        ) if isinstance(artist_entries, list) else ""
        ident = track.get("id", item.get("id"))
        if not isinstance(ident, (str, int)) or not str(ident).isdigit():
            continue
        if not isinstance(title, str) or not title.strip() or not artists:
            continue
        album = track.get("albums")
        album_id = album[0].get("id") if isinstance(album, list) and album and isinstance(album[0], dict) else None
        url = (f"https://music.yandex.ru/album/{album_id}/track/{ident}"
               if isinstance(album_id, int) and album_id > 0 else
               f"https://music.yandex.ru/track/{ident}")
        when = item.get("timestamp")
        try:
            songs.append(SourceSong(str(ident), title[:350].strip(),
                                    artists[:300], url,
                                    when[:50] if isinstance(when, str) else None))
        except TasteSourceError:
            continue
    if not songs or len(songs) < max(1, len(tracks) * .80):
        raise TasteSourceError("В плейлисте слишком мало доступных метаданных — синхронизация отменена")
    return list({s.external_id: s for s in songs}.values())


async def fetch_yandex_playlist(url: str, *, client: httpx.AsyncClient | None = None) -> list[SourceSong]:
    playlist = parse_yandex_url(url)
    endpoint = f"https://api.music.yandex.ru/playlist/{playlist}"
    owned = client is None
    session = client or httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5),
                                          follow_redirects=False, trust_env=False)
    try:
        async with session.stream("GET", endpoint,
                headers={"Accept": "application/json",
                         "x-yandex-music-without-invocation-info": "1",
                         "User-Agent": "private-musicbot-taste-sync/1.0"},
                follow_redirects=False) as response:
            if response.status_code != 200:
                raise TasteSourceError("Плейлист недоступен без авторизации или API Яндекса не отвечает")
            if int(response.headers.get("Content-Length", "0")) > MAX_SOURCE_BYTES:
                raise TasteSourceError("Плейлист слишком большой")
            payload = bytearray()
            async for chunk in response.aiter_bytes():
                payload.extend(chunk)
                if len(payload) > MAX_SOURCE_BYTES:
                    raise TasteSourceError("Плейлист слишком большой")
        if "json" not in response.headers.get("Content-Type", ""):
            raise TasteSourceError("Яндекс прислал неожиданный формат ответа")
        return parse_yandex_playlist(json.loads(payload))
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise TasteSourceError("Ошибка загрузки плейлиста Яндекс Музыки") from exc
    finally:
        if owned:
            await session.aclose()


def _youtube_link(video_id: str | None, artist: str, title: str) -> str:
    return (f"https://music.youtube.com/watch?v={video_id}" if video_id
            else "https://music.youtube.com/search?q=" + quote(f"{artist} {title}"))


def _song_id(artist: str, title: str) -> str:
    return "title-" + hashlib.sha256(
        (artist.strip().casefold() + "\x00" + title.strip().casefold()).encode("utf-8")
    ).hexdigest()[:32]


def parse_youtube_csv(raw: str) -> list[SourceSong]:
    """Google Takeout Music Library songs (titles) or a labeled CSV export."""
    reader = csv.DictReader(io.StringIO(raw.lstrip("\ufeff")))
    if not reader.fieldnames or len(reader.fieldnames) > 64:
        raise TasteSourceError("В CSV нет заголовков")
    aliases = {str(k).strip().casefold(): k for k in reader.fieldnames}
    def field(row, *keys):
        for key in keys:
            col = aliases.get(key.casefold())
            if col is not None and row.get(col):
                return str(row[col]).strip()
        return ""
    has_title = any(k in aliases for k in ("song title", "title", "video title", "track name"))
    has_artist = any(k in aliases for k in ("artist name", "artist", "artists", "channel name"))
    if not (has_title and has_artist):
        raise TasteSourceError(
            "В CSV есть только ID видео. Выгрузи раздел YouTube Music → music library songs "
            "в Google Takeout или пришли текст страницы с названиями и исполнителями."
        )
    songs = []
    for row in reader:
        if len(songs) > MAX_SOURCE_TRACKS:
            raise TasteSourceError("Слишком много треков в CSV")
        title = field(row, "Song Title", "Video Title", "Track Name", "Title")
        artist = field(row, "Artist Name", "Artist", "Artists", "Channel Name")
        ident = field(row, "Video ID", "videoId", "Video Id")
        if not title or not artist:
            continue
        vid = ident if VIDEO_ID.fullmatch(ident) else None
        key = vid or _song_id(artist, title)
        when = field(row, "Playlist Video Creation Timestamp", "Date Added", "Added At")
        try:
            songs.append(SourceSong(key, title[:350], artist[:300],
                                    _youtube_link(vid, artist, title), when or None))
        except TasteSourceError:
            continue
    if not songs:
        raise TasteSourceError("Не найдено песен с названиями и исполнителями")
    return list({song.external_id: song for song in songs}.values())


def parse_youtube_page_text(raw: str) -> list[SourceSong]:
    """Copied YouTube Music playlist page: title, artist(s), album, duration.

    This is a *partial snapshot*: copying a page may include fewer entries than
    its visible total (lazy-loaded virtualized rows). Never infer removals.
    """
    rows = [x.strip() for x in raw.replace("\r", "").split("\n") if x.strip()]
    if len(rows) > 50000:
        raise TasteSourceError("Слишком большой текст")
    # Remove navigation by finding the first duration marker and taking the last
    # 3 lines before it. Subsequent tracks begin immediately after the duration.
    songs = []
    last = 0
    for i, line in enumerate(rows):
        if not DURATION.fullmatch(line):
            continue
        block = rows[last:i]
        last = i + 1
        if len(block) < 2:
            continue
        if not songs and len(block) > 3:
            # The first row often follows page navigation, genres and headers.
            block = block[-3:]
        if len(block) > 14:
            continue
        title, artist = block[0], block[1]
        if title.startswith(("http://", "https://")) or len(title) > 350:
            continue
        # Multiple artists sometimes occupy separate rows around "," and "и".
        if len(block) > 3:
            names = [name for name in block[1:-1] if name not in {",", "и", "&"}]
            if names:
                artist = ", ".join(names)
        if not title or not artist or len(artist) > 300:
            continue
        try:
            songs.append(SourceSong(_song_id(artist, title), title, artist,
                                    _youtube_link(None, artist, title)))
        except TasteSourceError:
            continue
    if not songs:
        raise TasteSourceError("Не удалось извлечь треки из скопированной страницы YouTube Music")
    return list({song.external_id: song for song in songs}.values())


def parse_youtube_export(data: bytes, filename: str) -> tuple[list[SourceSong], bool]:
    """(songs, complete snapshot). Unknown/partial page never deletes old likes."""
    if not data or len(data) > MAX_SOURCE_BYTES:
        raise TasteSourceError("Файл пуст или превышает 24 МиБ")
    if not filename.lower().endswith((".txt", ".csv")):
        raise TasteSourceError("Поддерживаются только CSV/TXT; не присылай ZIP с личными данными")
    try:
        raw = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TasteSourceError("Файл должен быть в UTF-8") from exc
    if filename.lower().endswith(".csv"):
        # Takeout Music Library Songs is not identical to the liked-videos playlist.
        # Even a complete CSV must not erase independently imported favorites.
        return parse_youtube_csv(raw), False
    return parse_youtube_page_text(raw), False
