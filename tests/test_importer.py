"""Networkless bounded MP3/M4A import into Telegram asset store."""
import asyncio
from pathlib import Path

import httpx
import pytest

from music_bot.catalog import CatalogTrack
from music_bot.importer import (
    AudioIngestService, MediaImportError, temporary_audio,
    validate_direct_audio_url,
)
from music_bot.storage import Library

RECORDING = "8a49dba0-253a-4535-b87f-78bb035336ce"
HOSTS = ("audio.example.org",)
URL = "https://audio.example.org/records/file.mp3?signature=example"


def test_host_and_protocol_validation():
    assert validate_direct_audio_url(URL, HOSTS) == ".mp3"
    assert validate_direct_audio_url(
        "https://audio.example.org/sample.m4a", HOSTS
    ) == ".m4a"
    for bad in [
        "http://audio.example.org/records/file.mp3",
        "https://evil.example.org/records/file.mp3",
        "https://audio.example.org.evil.net/records/file.mp3",
        "https://audio.example.org:444/records/file.mp3",
        "https://user:pass@audio.example.org/records/file.mp3",
        "https://audio.example.org/sample.html",
        "https://127.0.0.1/music.mp3",
        "https://audio.example.org/sample.mp3#fragment",
    ]:
        with pytest.raises(MediaImportError):
            validate_direct_audio_url(bad, HOSTS)
    with pytest.raises(MediaImportError):
        validate_direct_audio_url(URL, ())


def test_stream_download_with_magic_validation_and_cleanup():
    paths = []

    def handler(request):
        assert request.url.host == "audio.example.org"
        return httpx.Response(
            200, headers={"content-type": "audio/mpeg"},
            content=b"ID3" + b"\x00" * 400,
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            async with temporary_audio(URL, HOSTS, client=http, limit=1024) as path:
                paths.append(path)
                assert path.exists()
                assert path.read_bytes().startswith(b"ID3")
            assert not paths[0].exists()

    asyncio.run(scenario())


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"location": "https://evil.net/foo.mp3"}),
    httpx.Response(200, headers={"content-type":"text/html"}, text="<html>bad</html>"),
    httpx.Response(
        200, headers={"content-type":"audio/mpeg", "content-length":"2000"},
        content=b"ID3" + b"\x00" * 2000,
    ),
    httpx.Response(
        200, headers={"content-type":"audio/mpeg"},
        content=b"not a real mp3 file",
    ),
])
def test_wrong_redirect_mime_size_and_signature_rejected(response):
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: response),
        ) as http:
            with pytest.raises(MediaImportError):
                async with temporary_audio(
                    URL, HOSTS, client=http, limit=1000,
                ) as path:
                    pass

    asyncio.run(scenario())


def test_m4a_magic_header_and_url_restrictions():
    mp4 = (24).to_bytes(4, "big") + b"ftyp" + b"M4A " + b"\0" * 12

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "audio/mp4"}, content=mp4,
            )
        )) as http:
            async with temporary_audio(
                "https://audio.example.org/sample.m4a", HOSTS, client=http
            ) as path:
                assert path.read_bytes() == mp4

    asyncio.run(scenario())


def test_import_links_metadata_to_real_telegram_file_and_reuses(tmp_path):
    db = Library(tmp_path / "db.sqlite")
    db.remember_candidates([CatalogTrack(RECORDING, "Roads", "Portishead")])
    sends = []
    file_paths = []

    def handler(request):
        return httpx.Response(
            200, headers={"content-type": "audio/mpeg"},
            content=b"ID3" + b"\x00" * 200,
        )

    async def publish(path: Path, title: str, artist: str):
        assert title == "Roads" and artist == "Portishead"
        file_paths.append(path)
        sends.append(title)
        return "TGFILE", "TGUNIQUE"

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            svc = AudioIngestService(db, HOSTS, publish, client=http)
            first = await svc.import_recording(RECORDING, URL)
            second = await svc.import_recording(RECORDING, URL)
            assert first["sent"] is True and first["reused"] is False
            assert second["sent"] is False and second["reused"] is True
            assert first["track_id"] == second["track_id"]
        assert len(sends) == 1
        assert not file_paths[0].exists()

    asyncio.run(scenario())
    t = db.get_track(db.linked_candidate_tracks()[RECORDING])
    assert t.file_id == "TGFILE" and t.file_unique_id == "TGUNIQUE"
    assert len(db.all_tracks()) == 1


def test_failed_publisher_never_links_or_leaves_temporary_media(tmp_path):
    db = Library(tmp_path / "music.db")
    db.remember_candidates([CatalogTrack(RECORDING, "Roads", "Portishead")])
    files = []

    async def fail(path, title, artist):
        files.append(path)
        raise MediaImportError("Telegram not available")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, headers={"content-type":"audio/mpeg"},
                content=b"ID3" + b"\x00" * 100,
            )
        )) as http:
            svc = AudioIngestService(db, HOSTS, fail, client=http)
            with pytest.raises(MediaImportError):
                await svc.import_recording(RECORDING, URL)

    asyncio.run(scenario())
    assert len(files) == 1 and not files[0].exists()
    assert db.linked_candidate_tracks() == {}
    assert db.all_tracks() == []


def test_unknown_catalog_entry_cannot_be_downloaded(tmp_path):
    db = Library(tmp_path / "music.db")

    async def should_never_publish(path, title, artist):
        raise AssertionError("unknown candidates must be rejected")

    async def scenario():
        svc = AudioIngestService(db, HOSTS, should_never_publish)
        with pytest.raises(MediaImportError):
            await svc.import_recording(RECORDING, URL)

    asyncio.run(scenario())
