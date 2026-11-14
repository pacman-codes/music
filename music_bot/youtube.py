"""Optional YouTube search and bounded MP3 import for licensed downloads.

Runs python -m yt_dlp as a subprocess; does not pass the Telegram token,
cookies or arbitrary source URLs. Output is native sendAudio only after upload.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable

from music_bot.catalog import valid_mbid
from music_bot.importer import MAX_AUDIO_BYTES, _verify_file_signature
from music_bot.storage import Library

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
MAX_SEARCH_BYTES = 1_000_000
MAX_TEMP_BYTES = 80 * 1024 * 1024
MAX_DURATION = 1200
MAX_SEARCH = 8


class YouTubeError(RuntimeError):
    """Safe error; never show yt-dlp stderr or source credentials."""


@dataclass(frozen=True)
class VideoCandidate:
    video_id: str
    title: str
    channel: str
    duration_sec: int | None

    def public(self) -> dict:
        return {
            "video_id": self.video_id, "title": self.title,
            "channel": self.channel, "duration_sec": self.duration_sec,
            "playable": False,
        }


def validate_video_id(video_id: str) -> str:
    if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id):
        raise YouTubeError("Неверный идентификатор YouTube")
    return video_id


def parse_search_response(data: object, limit: int = MAX_SEARCH) -> list[VideoCandidate]:
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise YouTubeError("YouTube не вернул список результатов")
    result, seen = [], set()
    for item in data["entries"]:
        if not isinstance(item, dict):
            continue
        video_id = item.get("id")
        if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id):
            continue
        if video_id in seen or item.get("live_status") in {"is_live", "is_upcoming"}:
            continue
        raw_duration = item.get("duration")
        duration = (int(raw_duration)
                    if type(raw_duration) in (int, float)
                    and 0 < raw_duration <= MAX_DURATION else None)
        if raw_duration is not None and duration is None:
            continue
        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        channel = item.get("channel") or item.get("uploader") or "YouTube"
        if not isinstance(channel, str):
            channel = "YouTube"
        result.append(VideoCandidate(
            video_id, title.strip()[:220], channel.strip()[:150], duration
        ))
        seen.add(video_id)
        if len(result) >= limit:
            break
    return result


def temporary_directory_bytes(directory: Path) -> int:
    return sum(
        file.stat().st_size for file in directory.iterdir()
        if file.is_file() and not file.is_symlink()
    )


async def run_ytdlp(args: list[str], *, timeout: float,
                    output_dir: Path | None = None) -> bytes:
    """Bounded subprocess without shell execution or arbitrary URLs."""
    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "yt_dlp", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        raise YouTubeError("yt-dlp не установлен на сервере") from exc
    task = asyncio.create_task(process.communicate())
    deadline = time.monotonic() + timeout
    try:
        while not task.done():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise YouTubeError("Превышено время работы YouTube-источника")
            if (output_dir is not None and output_dir.exists()
                    and temporary_directory_bytes(output_dir) > MAX_TEMP_BYTES):
                raise YouTubeError("YouTube-источник превысил лимит диска")
            try:
                await asyncio.wait_for(asyncio.shield(task), min(1.0, remaining))
            except TimeoutError:
                pass
        stdout, _stderr = task.result()
    except (YouTubeError, asyncio.CancelledError):
        if process.returncode is None:
            # ffmpeg is a yt-dlp child; kill the whole session on Linux,
            # not only yt-dlp, to avoid orphaned transcoding on small VPS.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        await task
        raise
    if process.returncode:
        raise YouTubeError("YouTube не разрешил получить данные или нужный формат")
    if len(stdout) > MAX_SEARCH_BYTES:
        raise YouTubeError("Слишком большой ответ YouTube-источника")
    return stdout


class YouTubeSource:
    async def search(self, query: str, *, limit: int = 6) -> list[VideoCandidate]:
        term = query.strip()
        if not 2 <= len(term) <= 90 or not 1 <= limit <= MAX_SEARCH:
            raise ValueError("YouTube search needs 2–90 chars and 1–8 hits")
        args = [
            "--ignore-config", "--flat-playlist",
            "--dump-single-json", "--skip-download", "--no-warnings",
            "--socket-timeout", "8", "--extractor-retries", "1",
            "--playlist-end", str(limit), "--", f"ytsearch{limit}:{term}",
        ]
        data = await run_ytdlp(args, timeout=25)
        try:
            payload = json.loads(data)
        except (ValueError, TypeError) as exc:
            raise YouTubeError("Некорректный результат поиска YouTube") from exc
        return parse_search_response(payload, limit=limit)


@asynccontextmanager
async def temporary_youtube_mp3(video_id: str) -> AsyncIterator[Path]:
    """Temporary transcoded MP3 wiped after a send, even if sending fails."""
    video_id = validate_video_id(video_id)
    with tempfile.TemporaryDirectory(prefix="music-youtube-") as directory:
        root = Path(directory)
        args = [
            "--ignore-config", "--no-playlist", "--no-progress",
            "--no-warnings", "--no-continue", "--no-overwrites",
            "--socket-timeout", "10", "--retries", "1",
            "--fragment-retries", "1", "--max-filesize", "40M",
            "--match-filter", f"duration <= {MAX_DURATION} & !is_live",
            "--extract-audio", "--audio-format", "mp3",
            "--audio-quality", "128K", "--restrict-filenames",
            "--output", str(root / "%(id)s.%(ext)s"),
            "--", f"https://www.youtube.com/watch?v={video_id}",
        ]
        await run_ytdlp(args, timeout=120, output_dir=root)
        files = list(root.glob("*.mp3"))
        if len(files) != 1 or files[0].is_symlink():
            raise YouTubeError("YouTube не выдал единственный MP3")
        result = files[0]
        if result.stat().st_size > MAX_AUDIO_BYTES or result.stat().st_size == 0:
            raise YouTubeError("Аудио превышает лимит Telegram")
        try:
            _verify_file_signature(result, ".mp3")
        except Exception as exc:
            raise YouTubeError("Некорректный MP3 после конвертации") from exc
        yield result


Publisher = Callable[[Path, str, str], Awaitable[tuple[str, str]]]


class YouTubeIngestService:
    def __init__(self, library: Library, publisher: Publisher):
        self.db = library
        self.publisher = publisher

    async def import_video(self, video_id: str, *,
                           recording_mbid: str | None = None) -> dict:
        """Ingest selected video; optionally bind an *explicitly selected* MBID.

        MusicBrainz search similarity must not silently determine audio identity.
        Two different YouTube videos targeting the same MBID also share a
        cross-process lease with direct imports of that recording.
        """
        video_id = validate_video_id(video_id)
        mbid = None
        recording = None
        if recording_mbid is not None:
            if not valid_mbid(recording_mbid):
                raise YouTubeError("Некорректный MusicBrainz ID")
            mbid = recording_mbid.lower()
            recording = self.db.get_candidate(mbid)
            if recording is None:
                raise YouTubeError("Сначала найди запись в музыкальном каталоге")

        video = self.db.get_youtube_video(video_id)
        if video is None:
            raise YouTubeError("Сначала найди ролик через /yt или Mini App")
        if video["duration_sec"] is not None and video["duration_sec"] > MAX_DURATION:
            raise YouTubeError("Видео длиннее 20 минут")

        lease = self.db.acquire_media_import("youtube", video_id)
        if lease is None:
            raise YouTubeError("Это видео уже загружается. Повтори позже.")
        mbid_lease = None
        try:
            if mbid is not None:
                mbid_lease = self.db.acquire_media_import("musicbrainz", mbid)
                if mbid_lease is None:
                    raise YouTubeError("Эта запись уже загружается. Повтори позже.")

            video = self.db.get_youtube_video(video_id)
            linked = video.get("track_id") if video else None
            if linked is not None:
                track = self.db.get_track(linked)
                if track is not None:
                    if mbid:
                        try:
                            self.db.associate_youtube_recording(video_id, mbid)
                        except ValueError as exc:
                            raise YouTubeError(str(exc)) from exc
                    return {"track_id": track.id, "reused": True}

            if mbid is not None:
                # Do not upload a duplicate asset merely because a second
                # YouTube result was chosen for an already playable recording.
                already = self.db.resolved_catalog_tracks().get(mbid)
                if already is not None and self.db.get_track(already) is not None:
                    raise YouTubeError("Эта запись уже сохранена в Telegram")

            # The old VPS is intentionally limited to one active transfer
            # across *all* bot and Mini App media workers.
            capacity_lease = self.db.acquire_media_import("global", "media")
            if capacity_lease is None:
                raise YouTubeError("Сервер сейчас загружает другой файл. Повтори позже.")
            try:
                title = recording["title"] if recording else video["title"]
                artist = recording["artist"] if recording else video["channel"]
                async with temporary_youtube_mp3(video_id) as file:
                    file_id, unique = await self.publisher(file, title, artist)
                    if not file_id or not unique:
                        raise YouTubeError("Telegram не подтвердил аудиофайл")
                    track = self.db.add_track(file_id, unique, title, artist)
                    self.db.link_youtube_video(video_id, track.id)
                    if mbid:
                        try:
                            self.db.associate_youtube_recording(video_id, mbid)
                        except ValueError as exc:
                            raise YouTubeError(str(exc)) from exc
                    return {"track_id": track.id, "reused": False}
            finally:
                self.db.release_media_import("global", "media", capacity_lease)
        finally:
            if mbid_lease is not None:
                self.db.release_media_import("musicbrainz", mbid, mbid_lease)
            self.db.release_media_import("youtube", video_id, lease)
