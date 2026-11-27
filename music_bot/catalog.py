"""Safe, cached discovery metadata from MusicBrainz and ListenBrainz.

This module deliberately NEVER downloads audio. A recording MBID is not a
Telegram file_id or a playable URL. Only two fixed HTTPS upstreams are used.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
import unicodedata
import uuid
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

MUSICBRAINZ = "https://musicbrainz.org"
LISTENBRAINZ = "https://api.listenbrainz.org"
USER_AGENT = "music-personal-bot/0.4.0 (https://github.com/pacman-codes/music)"
MAX_RESPONSE_BYTES = 1_000_000
ALBUM_STALE_SECONDS = 7 * 86400
logger = logging.getLogger(__name__)
VERSION_WORDS = ("live", "remix", "karaoke", "nightcore", "sped up",
                 "slowed", "instrumental", "cover version", "demo")


class CatalogError(RuntimeError):
    """A safe-to-display catalog lookup error, without URLs or secrets."""


@dataclass(frozen=True)
class CatalogTrack:
    mbid: str
    title: str
    artist: str
    artist_mbid: str | None = None
    duration_ms: int | None = None
    variant: bool = False

    def public(self) -> dict[str, Any]:
        """Public *metadata*, not a file, download URL or Telegram asset."""
        return {**asdict(self), "playable": False}


def normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def normalized_identity(title: str, artist: str) -> tuple[str, str]:
    return normalize(title), normalize(artist)


def is_variant(title: str) -> bool:
    name = normalize(title)
    return any(re.search(r"(?<!\w)" + re.escape(token) + r"(?!\w)", name)
               for token in VERSION_WORDS)


def valid_mbid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value.lower()
    except (TypeError, ValueError, AttributeError):
        return False


def _get_artist_credit(parts: Any) -> tuple[str, str | None]:
    if not isinstance(parts, list):
        return "", None
    artist_name = ""
    artist_id = None
    display: list[str] = []
    for item in parts:
        if isinstance(item, str):
            display.append(item)
        elif isinstance(item, dict):
            nested_artist = item.get("artist")
            fallback_name = nested_artist.get("name") if isinstance(nested_artist, dict) else ""
            name = item.get("name") or fallback_name or ""
            if not isinstance(name, str):
                continue
            display.append(name)
            if not artist_name:
                artist_name = name
            if not artist_id and isinstance(item.get("artist"), dict):
                artist_id = item["artist"].get("id")
            phrase = item.get("joinphrase")
            if isinstance(phrase, str):
                display.append(phrase)
    return "".join(display).strip() or artist_name, (
        artist_id if isinstance(artist_id, str) and valid_mbid(artist_id) else None
    )


def parse_recording(item: Any) -> CatalogTrack | None:
    if not isinstance(item, dict):
        return None
    mbid, title = item.get("id"), item.get("title")
    if not isinstance(mbid, str) or not valid_mbid(mbid):
        return None
    if not isinstance(title, str) or not title.strip():
        return None
    artist, artist_mbid = _get_artist_credit(item.get("artist-credit"))
    if not artist:
        return None
    duration = item.get("length")
    if type(duration) is not int or duration < 0 or duration > 14_400_000:
        duration = None
    return CatalogTrack(
        mbid=mbid.lower(), title=title.strip()[:250], artist=artist[:250],
        artist_mbid=artist_mbid, duration_ms=duration,
        variant=is_variant(title),
    )


def unique_tracks(tracks: list[CatalogTrack], limit: int,
                  *, allow_variants: bool) -> list[CatalogTrack]:
    """Keep one good recording per artist/title, preserving input ranking."""
    seen: set[tuple[str, str]] = set()
    result = []
    for track in tracks:
        if not allow_variants and track.variant:
            continue
        key = normalized_identity(track.title, track.artist)
        if key in seen:
            continue
        seen.add(key)
        result.append(track)
        if len(result) >= limit:
            break
    return result


class CatalogCache:
    def __init__(self, database: Path | str):
        self.path = Path(database)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._db()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS external_catalog_cache (
                cache_key TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS external_catalog_expires "
                       "ON external_catalog_cache(expires_at)")
            db.execute("""CREATE TABLE IF NOT EXISTS external_catalog_throttle (
                provider TEXT PRIMARY KEY,
                next_allowed REAL NOT NULL
            )""")

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def reserve_request_slot(self, provider: str, interval: float = 1.1,
                             *, now: float | None = None) -> float:
        """Cross-process MusicBrainz limiter shared by bot and web processes.

        Reserve a slot under BEGIN IMMEDIATE before contacting MusicBrainz.
        Returns seconds to sleep; skipping a reserved slot is harmless.
        """
        if provider not in {"mb", "anilist", "tmdb", "acoustid"} or not 0.3 <= interval <= 5.0:
            raise ValueError("Unsupported rate-limit provider")
        timestamp = time.time() if now is None else now
        with closing(self._db()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT next_allowed FROM external_catalog_throttle WHERE provider=?",
                (provider,),
            ).fetchone()
            reserved = max(timestamp, row[0] if row is not None else timestamp)
            db.execute(
                """INSERT INTO external_catalog_throttle(provider,next_allowed)
                   VALUES(?,?)
                   ON CONFLICT(provider) DO UPDATE SET
                     next_allowed=excluded.next_allowed""",
                (provider, reserved + interval),
            )
            db.commit()
        return max(0.0, reserved - timestamp)

    def get(self, key: str, now: int | None = None, *, max_stale: int = 0) -> Any | None:
        timestamp = int(time.time()) if now is None else now
        with closing(self._db()) as db:
            row = db.execute(
                "SELECT payload FROM external_catalog_cache "
                "WHERE cache_key=? AND expires_at>?", (key, timestamp - max_stale)
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except (ValueError, TypeError):
            return None

    def put(self, key: str, value: Any, ttl: int, now: int | None = None) -> None:
        if not 1 <= ttl <= 30 * 86400:
            raise ValueError("TTL out of range")
        timestamp = int(time.time()) if now is None else now
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if len(payload.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise CatalogError("Слишком большой ответ музыкального каталога")
        with closing(self._db()) as db, db:
            # Keep expired release metadata briefly for outage-only fallback.
            # Other providers retain their existing expiration semantics.
            db.execute(
                """DELETE FROM external_catalog_cache WHERE expires_at<=?
                   AND (cache_key NOT LIKE ? OR expires_at<=?)""",
                (timestamp, '["mb","/ws/2/release/%', timestamp - ALBUM_STALE_SECONDS),
            )
            db.execute(
                """INSERT INTO external_catalog_cache(cache_key,payload,expires_at)
                   VALUES(?,?,?) ON CONFLICT(cache_key) DO UPDATE SET
                   payload=excluded.payload, expires_at=excluded.expires_at""",
                (key, payload, timestamp + ttl),
            )


class MusicCatalog:
    """Owner-only catalog client with fixed hosts and strict resource bounds."""

    def __init__(self, database: Path | str, client: httpx.AsyncClient | None = None):
        self.cache = CatalogCache(database)
        self._owned_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=4.0), follow_redirects=False,
            trust_env=False,
        )
        self._mb_lock = asyncio.Lock()

    async def close(self) -> None:
        if self._owned_client:
            await self.client.aclose()

    async def _json(self, provider: str, path: str, params: dict[str, Any],
                    ttl: int = 86400) -> Any:
        if provider not in {"mb", "lb"} or not path.startswith("/") or "//" in path:
            raise ValueError("Unsupported upstream")
        # Never construct a hostname from user input or accept upstream redirects.
        root = MUSICBRAINZ if provider == "mb" else LISTENBRAINZ
        key = json.dumps([provider, path, sorted(params.items())],
                         sort_keys=True, separators=(",", ":"))
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        if provider == "mb":
            async with self._mb_lock:
                cached = self.cache.get(key)
                if cached is not None:
                    return cached
                # Reserve a single shared slot across polling bot and web API,
                # even though these run as distinct processes.
                wait = self.cache.reserve_request_slot("mb", interval=1.10)
                if wait > 0:
                    await asyncio.sleep(wait)
                try:
                    value = await self._fetch(root + path, params)
                except CatalogError:
                    if path.startswith("/ws/2/release/"):
                        stale = self.cache.get(key, max_stale=ALBUM_STALE_SECONDS)
                        if isinstance(stale, dict) and (stale.get("releases") or stale.get("media")):
                            logger.warning("MusicBrainz release lookup failed; using cached metadata")
                            return stale
                    raise
        else:
            value = await self._fetch(root + path, params)
        self.cache.put(key, value, ttl)
        return value

    async def _fetch(self, url: str, params: dict[str, Any]) -> Any:
        try:
            async with self.client.stream(
                "GET", url, params=params,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                follow_redirects=False,
            ) as response:
                if response.status_code == 204:
                    return {}
                if response.status_code != 200:
                    logger.warning("Music catalog HTTP %s from %s", response.status_code, response.url.host)
                    raise CatalogError("Музыкальный каталог временно недоступен")
                if int(response.headers.get("content-length", "0")) > MAX_RESPONSE_BYTES:
                    raise CatalogError("Слишком большой ответ музыкального каталога")
                output = bytearray()
                async for chunk in response.aiter_bytes():
                    output.extend(chunk)
                    if len(output) > MAX_RESPONSE_BYTES:
                        raise CatalogError("Слишком большой ответ музыкального каталога")
                if "json" not in response.headers.get("content-type", ""):
                    raise CatalogError("Каталог прислал некорректный формат")
                return json.loads(output)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Music catalog request failed: %s", type(exc).__name__)
            raise CatalogError("Нет соединения с музыкальным каталогом") from exc

    async def search(self, text: str, limit: int = 15,
                     *, allow_variants: bool = True) -> list[CatalogTrack]:
        term = text.strip()
        if not 2 <= len(term) <= 100 or not 1 <= limit <= 25:
            raise ValueError("Search needs 2–100 characters and 1–25 results")
        # Escape Lucene operators: the owner enters plain text, never query syntax.
        escaped = re.sub(r'([+\-!(){}\[\]^"~*?:\\/])', r'\\\1', term)
        payload = await self._json(
            "mb", "/ws/2/recording/",
            {"query": escaped, "fmt": "json", "limit": min(70, limit * 4)},
        )
        if not isinstance(payload, dict):
            raise CatalogError("Неожиданный ответ MusicBrainz")
        rows = payload.get("recordings", [])
        if not isinstance(rows, list):
            raise CatalogError("Неожиданный формат списка треков")
        parsed = [track for item in rows if (track := parse_recording(item))]
        return unique_tracks(parsed, limit, allow_variants=allow_variants)

    async def artist_radio(self, name: str, *, difficulty: str = "medium",
                           limit: int = 12) -> list[CatalogTrack]:
        artist = name.strip()
        if not 2 <= len(artist) <= 90 or not 1 <= limit <= 20:
            raise ValueError("Artist name or result count out of range")
        if difficulty not in {"easy", "medium", "hard"}:
            raise ValueError("Invalid radio difficulty")
        escaped = artist.replace("\\", "\\\\").replace('"', '\\"')
        found = await self._json(
            "mb", "/ws/2/artist/",
            {"query": f'artist:"{escaped}"', "fmt": "json", "limit": 8},
            ttl=7 * 86400,
        )
        artists = found.get("artists", []) if isinstance(found, dict) else []
        # Prefer a name match, not a famous partial-name coincidence.
        exact = next((a for a in artists if isinstance(a, dict)
                      and normalize(str(a.get("name", ""))) == normalize(artist)), None)
        seed = exact or next((a for a in artists if isinstance(a, dict)
                              and valid_mbid(a.get("id"))), None)
        if seed is None or not valid_mbid(seed.get("id")):
            return []

        return await self.artist_radio_by_id(seed["id"], difficulty=difficulty,
                                             limit=limit)

    async def artist_radio_by_id(self, artist_mbid: str, *, difficulty: str = "medium",
                                 limit: int = 12) -> list[CatalogTrack]:
        """Discover records by a *confirmed* MusicBrainz artist identifier.

        Avoids fuzzy resolution when the owner has matched the seed track.
        """
        if not valid_mbid(artist_mbid) or difficulty not in {"easy", "medium", "hard"}:
            raise ValueError("Invalid artist MBID or radio mode")
        if not 1 <= limit <= 20:
            raise ValueError("Invalid radio result count")
        result = await self._json(
            "lb", f'/1/lb-radio/artist/{artist_mbid.lower()}',
            {"mode": difficulty, "max_similar_artists": 6,
             "max_recordings_per_artist": 5, "pop_begin": 0, "pop_end": 100},
            ttl=86400,
        )
        # LB artist radio groups recording entries by artist MBID.
        groups = []
        if isinstance(result, dict):
            for entries in result.values():
                if isinstance(entries, list):
                    groups.append([item for item in entries if isinstance(item, dict)])
        if not groups:
            return []
        # Round-robin across related artists prevents 10 songs of one artist.
        mbids: list[str] = []
        seen_ids: set[str] = set()
        for column in range(max(len(group) for group in groups)):
            for group in groups:
                if column >= len(group):
                    continue
                value = group[column].get("recording_mbid")
                if isinstance(value, str) and valid_mbid(value) and value not in seen_ids:
                    seen_ids.add(value)
                    mbids.append(value)
                if len(mbids) >= max(30, limit * 2):
                    break
            if len(mbids) >= max(30, limit * 2):
                break
        if not mbids:
            return []

        # One MB request resolves all recording MBIDs; never N requests per song.
        query = " OR ".join(f"rid:{mbid}" for mbid in mbids)
        recordings = await self._json(
            "mb", "/ws/2/recording/",
            {"query": query, "fmt": "json", "limit": len(mbids)},
            ttl=7 * 86400,
        )
        rows = recordings.get("recordings", []) if isinstance(recordings, dict) else []
        lookup = {}
        if isinstance(rows, list):
            for row in rows:
                parsed = parse_recording(row)
                if parsed is not None:
                    lookup[parsed.mbid] = parsed
        # Preserve LB collaborative order instead of MusicBrainz search score.
        selected = [lookup[mbid] for mbid in mbids if mbid in lookup]
        return unique_tracks(selected, limit, allow_variants=False)
