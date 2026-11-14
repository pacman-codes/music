"""Bounded import of owner-approved DIRECT audio links into Telegram.

Not a scraper, YouTube downloader or generic URL proxy. Sources must be
allowlisted HTTPS hosts under operator control. Temporary files are deleted.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable
from urllib.parse import urlsplit

import httpx

from music_bot.storage import Library

MAX_AUDIO_BYTES = 45 * 1024 * 1024
SAFE_HOST = re.compile(r"^(?:[a-z0-9][a-z0-9-]{0,62}\.)+[a-z0-9][a-z0-9-]{1,62}$")
SUPPORTED_EXTENSIONS = {".mp3", ".m4a"}
SUPPORTED_MIME = {
    "audio/mpeg", "audio/mp3", "audio/mp4", "audio/m4a",
    "audio/x-m4a", "application/octet-stream",
}


class MediaImportError(RuntimeError):
    """Safe importer error with no source URL or sensitive details."""


def validate_direct_audio_url(url: str, allowed_hosts: tuple[str, ...]) -> str:
    if not isinstance(url, str) or not 12 <= len(url) <= 2048:
        raise MediaImportError("Неверная ссылка на аудиофайл")
    try:
        target = urlsplit(url)
        host = (target.hostname or "").lower().rstrip(".")
        port = target.port
    except ValueError as exc:
        raise MediaImportError("Некорректный адрес источника") from exc
    allowed = {h.strip().lower() for h in allowed_hosts}
    if not allowed or target.scheme.lower() != "https":
        raise MediaImportError("Импорт отключён либо требуется HTTPS")
    if (not SAFE_HOST.fullmatch(host) or host not in allowed or
        port not in (None, 443) or target.username is not None or
        target.password is not None or target.fragment):
        raise MediaImportError("Этот хост не разрешён для импорта")
    if any(not SAFE_HOST.fullmatch(h) for h in allowed):
        raise MediaImportError("Недопустимый хост в конфигурации")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise MediaImportError("IP-адреса запрещены")
    if host.endswith((".localhost", ".local", ".internal", ".lan")):
        raise MediaImportError("Локальные адреса запрещены")
    extension = Path(target.path).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise MediaImportError("Поддерживаются только прямые .mp3 и .m4a")
    return extension


def _verify_file_signature(path: Path, extension: str) -> None:
    with path.open("rb") as file:
        magic = file.read(16)
    if extension == ".mp3":
        valid = (magic.startswith(b"ID3") or
                 (len(magic) >= 2 and magic[0] == 0xFF and magic[1] & 0xE0 == 0xE0))
    else:
        valid = len(magic) >= 12 and magic[4:8] == b"ftyp"
    if not valid:
        raise MediaImportError("Получен файл другого типа, а не MP3/M4A")


@asynccontextmanager
async def temporary_audio(
    url: str, hosts: tuple[str, ...], *,
    client: httpx.AsyncClient | None = None, limit: int = MAX_AUDIO_BYTES,
) -> AsyncIterator[Path]:
    """Bounded streaming download to temporary disk, removed even on errors."""
    extension = validate_direct_audio_url(url, hosts)
    if not 1 <= limit <= MAX_AUDIO_BYTES:
        raise ValueError("Invalid temporary audio limit")
    own_client = client is None
    http = client or httpx.AsyncClient(
        timeout=httpx.Timeout(20.0, connect=5.0), follow_redirects=False,
        trust_env=False,
    )
    try:
        # Bound the entire transfer; read timeout alone does not stop a source
        # sending a tiny chunk every few seconds forever.
        async with asyncio.timeout(90):
            with tempfile.TemporaryDirectory(prefix="music-import-") as tmp:
                target = Path(tmp) / f"audio{extension}"
                try:
                    async with http.stream(
                        "GET", url, follow_redirects=False,
                        headers={"Accept": "audio/mpeg,audio/mp4,application/octet-stream"},
                    ) as response:
                        if response.status_code != 200:
                            raise MediaImportError("Источник не выдал аудиофайл")
                        mime = response.headers.get("content-type", "").split(";", 1)[0].lower()
                        if mime not in SUPPORTED_MIME:
                            raise MediaImportError("Источник возвращает не аудиофайл")
                        declared = response.headers.get("content-length", "")
                        if declared:
                            try:
                                if int(declared) > limit:
                                    raise MediaImportError("Аудио превышает лимит 45 МБ")
                            except ValueError as exc:
                                raise MediaImportError("Неверный размер аудиофайла") from exc
                        actual = 0
                        with target.open("wb") as out:
                            async for chunk in response.aiter_bytes(65536):
                                actual += len(chunk)
                                if actual > limit:
                                    raise MediaImportError("Аудио превышает лимит 45 МБ")
                                out.write(chunk)
                        if actual == 0:
                            raise MediaImportError("Источник прислал пустой аудиофайл")
                        _verify_file_signature(target, extension)
                except httpx.HTTPError as exc:
                    raise MediaImportError("Не удалось получить аудиофайл") from exc
                yield target
    except TimeoutError as exc:
        raise MediaImportError("Превышено время загрузки аудиофайла") from exc
    finally:
        if own_client:
            await http.aclose()


Publisher = Callable[[Path, str, str], Awaitable[tuple[str, str]]]


class AudioIngestService:
    """Connect an allowlisted media source with a Telegram Bot API audio asset."""

    def __init__(self, library: Library, hosts: tuple[str, ...],
                 publisher: Publisher, *, client: httpx.AsyncClient | None = None):
        self.db = library
        self.hosts = hosts
        self.publisher = publisher
        self.client = client

    async def import_recording(self, mbid: str, url: str) -> dict:
        candidate = self.db.get_candidate(mbid)
        if candidate is None:
            raise MediaImportError("Сначала найди запись в музыкальном каталоге")
        if not self.hosts:
            raise MediaImportError("Импорт по ссылке не настроен")
        existing = candidate.get("track_id")
        if existing is not None and self.db.get_track(existing) is not None:
            return {"track_id": existing, "reused": True, "sent": False}
        # Never store the URL or hand it to Telegram directly: file is checked
        # and uploaded as native Telegram audio via Bot API by the publisher.
        async with temporary_audio(url, self.hosts, client=self.client) as path:
            file_id, file_unique_id = await self.publisher(
                path, candidate["title"], candidate["artist"],
            )
            if not file_id or not file_unique_id:
                raise MediaImportError("Telegram не вернул идентификатор аудио")
            track = self.db.add_track(
                file_id, file_unique_id, candidate["title"], candidate["artist"],
            )
            self.db.link_candidate_track(mbid, track.id)
            return {"track_id": track.id, "reused": False, "sent": True}
