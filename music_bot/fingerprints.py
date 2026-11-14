"""Optional safe Chromaprint/AcoustID identification of owner-uploaded audio.

No automatic identity linkage: even a high-scoring AcoustID result can
represent another master, live version or cover. Return candidates for a
separate explicit MusicBrainz confirmation.

AcoustID client key and Telegram file paths are never returned to the client.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from music_bot.catalog import CatalogCache, CatalogTrack, valid_mbid

ACOUSTID_URL = "https://api.acoustid.org/v2/lookup"
MAX_RESPONSE = 512 * 1024
MAX_AUDIO = 19 * 1024 * 1024
MAX_FINGERPRINT = 50000


class RecognitionError(RuntimeError):
    """Safe user-facing error without secrets or private Telegram file paths."""


@dataclass(frozen=True)
class RecognitionCandidate:
    recording: CatalogTrack
    confidence: float

    def public(self) -> dict:
        return {
            **self.recording.public(),
            "confidence": self.confidence,
            "match_confirmed": False,
        }


@dataclass(frozen=True)
class RecognitionResult:
    fingerprint_hash: str
    duration_sec: int
    candidates: tuple[RecognitionCandidate, ...]


def parse_fpcalc_output(data: bytes) -> tuple[int, str]:
    if len(data) > MAX_RESPONSE:
        raise RecognitionError("Chromaprint вернул слишком большой ответ")
    try:
        obj = json.loads(data)
    except (ValueError, TypeError) as exc:
        raise RecognitionError("Не удалось прочитать результат Chromaprint") from exc
    if not isinstance(obj, dict):
        raise RecognitionError("Пустой аудиоотпечаток")
    duration, fingerprint = obj.get("duration"), obj.get("fingerprint")
    if (type(duration) not in (int, float) or
            not 1 <= duration <= 24 * 3600 or
            not isinstance(fingerprint, str) or
            not 20 <= len(fingerprint) <= MAX_FINGERPRINT or
            not fingerprint.isascii()):
        raise RecognitionError("Некорректный аудиоотпечаток")
    return int(round(duration)), fingerprint


async def fpcalc(path: Path, *, timeout: float = 30) -> tuple[int, str]:
    if not path.is_file() or path.stat().st_size > MAX_AUDIO:
        raise RecognitionError("Аудиофайл отсутствует или слишком большой")
    if shutil.which("fpcalc") is None:
        raise RecognitionError("Chromaprint/fpcalc не установлен на сервере")
    try:
        proc = await asyncio.create_subprocess_exec(
            "fpcalc", "-json", str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as exc:
        raise RecognitionError("Не удалось запустить fpcalc") from exc
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError as exc:
        proc.kill()
        await proc.communicate()
        raise RecognitionError("Превышено время распознавания аудио") from exc
    if proc.returncode != 0:
        raise RecognitionError("Невозможно снять отпечаток с этого аудиофайла")
    return parse_fpcalc_output(stdout)


def parse_acoustid(data: Any, *, min_confidence: float = 0.55,
                   limit: int = 8) -> list[RecognitionCandidate]:
    if not isinstance(data, dict) or data.get("status") != "ok":
        raise RecognitionError("AcoustID вернул ошибку распознавания")
    values = data.get("results")
    if not isinstance(values, list):
        raise RecognitionError("AcoustID вернул некорректный ответ")
    best: dict[str, RecognitionCandidate] = {}
    for item in values[:30]:
        if not isinstance(item, dict):
            continue
        score = item.get("score")
        if type(score) not in (int, float) or not min_confidence <= score <= 1:
            continue
        recordings = item.get("recordings")
        if not isinstance(recordings, list):
            continue
        for record in recordings[:15]:
            if not isinstance(record, dict):
                continue
            mbid, title = record.get("id"), record.get("title")
            credits = record.get("artists")
            if not valid_mbid(mbid) or not isinstance(title, str) or not title.strip():
                continue
            artist = None
            if isinstance(credits, list):
                artist = " & ".join(
                    a["name"].strip() for a in credits[:5]
                    if isinstance(a, dict) and isinstance(a.get("name"), str)
                    and a["name"].strip()
                )
            if not artist:
                continue
            record_id = mbid.lower()
            previous = best.get(record_id)
            match = RecognitionCandidate(
                CatalogTrack(record_id, title.strip()[:220], artist[:160]),
                round(float(score), 3),
            )
            if previous is None or match.confidence > previous.confidence:
                best[record_id] = match
    return sorted(
        best.values(), key=lambda c: (-c.confidence, c.recording.mbid)
    )[:limit]


class AudioRecognition:
    def __init__(self, database: str | Path, client_key: str,
                 client: httpx.AsyncClient | None = None):
        self.client_key = client_key.strip()
        self.cache = CatalogCache(database)
        self._owned = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(10, connect=4),
            trust_env=False, follow_redirects=False,
        )

    async def close(self):
        if self._owned:
            await self.client.aclose()

    async def identify(self, path: Path) -> RecognitionResult:
        if not self.client_key:
            raise RecognitionError("AcoustID не настроен")
        duration, signature = await fpcalc(path)
        digest = hashlib.sha256(signature.encode("ascii")).hexdigest()
        cache_key = "acoustid:" + digest + ":" + str(duration)
        cached = self.cache.get(cache_key)
        if cached is None:
            wait = self.cache.reserve_request_slot("acoustid", interval=0.5)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with self.client.stream(
                    "POST", ACOUSTID_URL,
                    data={"client": self.client_key, "duration": str(duration),
                          "fingerprint": signature, "meta": "recordings",
                          "format": "json"},
                    follow_redirects=False,
                ) as response:
                    if response.status_code != 200:
                        raise RecognitionError(
                            "AcoustID сейчас не отвечает или отклонил ключ"
                        )
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_RESPONSE:
                            raise RecognitionError("Слишком большой ответ AcoustID")
                    cached = json.loads(content)
            except (httpx.HTTPError, ValueError) as exc:
                raise RecognitionError("Ошибка обращения к AcoustID") from exc
            # Validate before caching arbitrary upstream bytes.
            suggestions = parse_acoustid(cached)
            self.cache.put(cache_key, cached, ttl=7 * 24 * 3600)
        else:
            suggestions = parse_acoustid(cached)
        return RecognitionResult(digest, duration, tuple(suggestions))



async def recognize_telegram_track(db, bot, recognizer: AudioRecognition,
                                   track_id: int) -> dict:
    """Read at most 19 MiB from this bot's own Telegram file, then delete it.

    Does not assume ownership of arbitrary URLs and never assigns MusicBrainz
    identity automatically. Existing fingerprint matches are only duplicate
    *suggestions*; different encodings may have different hashes.
    """
    from tempfile import TemporaryDirectory

    track = db.get_track(track_id)
    if track is None:
        raise RecognitionError("Трек отсутствует в библиотеке")
    try:
        info = await bot.get_file(track.file_id)
        if (type(info.file_size) is not int or
                not 1 <= info.file_size <= MAX_AUDIO or
                not isinstance(info.file_path, str) or not info.file_path):
            raise RecognitionError(
                "Telegram не позволяет получить этот файл (лимит 19 МиБ)"
            )
        with TemporaryDirectory(prefix="music-fingerprint-") as name:
            path = Path(name) / "audio"
            await bot.download_file(info.file_path, destination=path, timeout=90)
            if not path.is_file() or not 1 <= path.stat().st_size <= MAX_AUDIO:
                raise RecognitionError("Скачанный файл не прошёл проверку размера")
            outcome = await recognizer.identify(path)
    except RecognitionError:
        raise
    except Exception as exc:
        # Never expose a Telegram file path, token, or raw Bot API error.
        raise RecognitionError("Не удалось получить аудио для распознавания") from exc
    duplicate_ids = db.save_audio_fingerprint(
        track_id, outcome.fingerprint_hash, outcome.duration_sec,
    )
    if outcome.candidates:
        db.remember_candidates([candidate.recording for candidate in outcome.candidates])
    return {
        "track_id": track_id,
        "duration_sec": outcome.duration_sec,
        "duplicates": duplicate_ids,
        "results": [candidate.public() for candidate in outcome.candidates],
        "auto_matched": False,
    }
