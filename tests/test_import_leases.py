"""Multiple processes share one SQLite lease for each media source.

No actual Telegram or public network; simultaneous Bot/Mini App workers
are simulated with independent Library objects pointing at the same file.
"""
import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest

import music_bot.youtube as youtube_module
from music_bot.catalog import CatalogTrack
from music_bot.importer import AudioIngestService, MediaImportError
from music_bot.storage import Library
from music_bot.youtube import VideoCandidate, YouTubeError, YouTubeIngestService

VIDEO = "dQw4w9WgXcQ"
MBID = "8a49dba0-253a-4535-b87f-78bb035336ce"
URL = "https://audio.example.org/song.mp3"


def test_lease_cross_process_ownership_and_expiry(tmp_path):
    filename = tmp_path / "music.sqlite"
    first = Library(filename)
    second = Library(filename)
    initial = first.acquire_media_import("youtube", VIDEO, ttl=5, now=100)
    assert initial
    assert second.acquire_media_import("youtube", VIDEO, ttl=5, now=100) is None
    later = second.acquire_media_import("youtube", VIDEO, ttl=5, now=106)
    assert later and later != initial
    assert first.release_media_import("youtube", VIDEO, initial) is False
    assert second.acquire_media_import("youtube", VIDEO, ttl=5, now=106) is None
    assert second.release_media_import("youtube", VIDEO, later) is True
    assert first.acquire_media_import("youtube", VIDEO, ttl=5, now=106)
    with pytest.raises(ValueError):
        first.acquire_media_import("untrusted", VIDEO)
    with pytest.raises(ValueError):
        first.acquire_media_import("youtube", VIDEO, ttl=100000)


def test_two_youtube_workers_only_upload_one_audio(tmp_path, monkeypatch):
    filename = tmp_path / "music.sqlite"
    bot_db = Library(filename)
    web_db = Library(filename)
    bot_db.remember_youtube_videos([VideoCandidate(VIDEO, "Roads", "Portishead", 301)])
    entered, allow_finish = asyncio.Event(), asyncio.Event()
    publishing = []

    @asynccontextmanager
    async def fake_download(video_id):
        file = tmp_path / "download.mp3"
        file.write_bytes(b"ID3" + b"\x00" * 90)
        try:
            yield file
        finally:
            file.unlink(missing_ok=True)

    async def uploader(path, title, artist):
        publishing.append(path)
        entered.set()
        await allow_finish.wait()
        return "tg-file", "tg-unique"

    monkeypatch.setattr(youtube_module, "temporary_youtube_mp3", fake_download)

    async def scenario():
        bot = YouTubeIngestService(bot_db, uploader)
        web = YouTubeIngestService(web_db, uploader)
        first_task = asyncio.create_task(bot.import_video(VIDEO))
        await entered.wait()
        with pytest.raises(YouTubeError, match="уже загружается"):
            await web.import_video(VIDEO)
        assert len(publishing) == 1
        allow_finish.set()
        first = await first_task
        second = await web.import_video(VIDEO)
        assert first["reused"] is False
        assert second["reused"] is True
        assert first["track_id"] == second["track_id"]
        assert len(publishing) == 1
        assert bot_db.get_youtube_video(VIDEO)["track_id"] == first["track_id"]

    asyncio.run(scenario())


def test_failed_youtube_upload_releases_lease(tmp_path, monkeypatch):
    db = Library(tmp_path / "music.sqlite")
    db.remember_youtube_videos([VideoCandidate(VIDEO, "Roads", "Portishead", 301)])

    @asynccontextmanager
    async def dummy_download(_):
        file = tmp_path / "download.mp3"
        file.write_bytes(b"ID3" + b"\x00" * 50)
        try:
            yield file
        finally:
            file.unlink(missing_ok=True)

    monkeypatch.setattr(youtube_module, "temporary_youtube_mp3", dummy_download)

    async def bad_publish(*_):
        raise YouTubeError("upstream failed")

    async def good_publish(*_):
        return "tg-file", "tg-unique"

    async def scenario():
        with pytest.raises(YouTubeError):
            await YouTubeIngestService(db, bad_publish).import_video(VIDEO)
        await YouTubeIngestService(db, good_publish).import_video(VIDEO)

    asyncio.run(scenario())
    assert db.get_youtube_video(VIDEO)["track_id"] is not None
    with db.con() as con:
        assert con.execute("SELECT COUNT(*) FROM media_import_leases").fetchone()[0] == 0


def test_direct_mp3_import_shared_lease_and_retry(tmp_path):
    filename = tmp_path / "library.db"
    first = Library(filename)
    second = Library(filename)
    first.remember_candidates([CatalogTrack(MBID, "Roads", "Portishead")])
    reached, allow_finish = asyncio.Event(), asyncio.Event()
    audio_calls = []

    def handler(request):
        return httpx.Response(200, headers={"content-type": "audio/mpeg"},
                              content=b"ID3" + b"\x00" * 200)

    async def publisher(path, _title, _artist):
        audio_calls.append(path)
        reached.set()
        await allow_finish.wait()
        return "telegram-file", "telegram-unique"

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            bot = AudioIngestService(first, ("audio.example.org",),
                                     publisher, client=client)
            web = AudioIngestService(second, ("audio.example.org",),
                                     publisher, client=client)
            future = asyncio.create_task(bot.import_recording(MBID, URL))
            await reached.wait()
            with pytest.raises(MediaImportError, match="уже загружается"):
                await web.import_recording(MBID, URL)
            allow_finish.set()
            first_result = await future
            again = await web.import_recording(MBID, URL)
            assert first_result["sent"] is True
            assert again["reused"] is True
            assert len(audio_calls) == 1
            assert first.resolved_catalog_tracks()[MBID] == first_result["track_id"]

    asyncio.run(scenario())
