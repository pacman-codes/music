"""Explicit MusicBrainz choice survives YouTube import into native Telegram.

No title-based matching is allowed to silently create recording links.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

import music_bot.youtube as youtube
from music_bot.catalog import CatalogTrack
from music_bot.storage import Library
from music_bot.telegram_youtube import youtube_keyboard
from music_bot.youtube import VideoCandidate, YouTubeError, YouTubeIngestService

VIDEO = "dQw4w9WgXcQ"
VIDEO2 = "M7lc1UVf-VE"
MBID = "8a49dba0-253a-4535-b87f-78bb035336ce"
OTHER = "f3bba4cd-8018-468b-902e-bc8f029593e5"


def setup(db: Library):
    db.remember_candidates([
        CatalogTrack(MBID, "Roads", "Portishead"),
        CatalogTrack(OTHER, "Glory Box", "Portishead"),
    ])
    db.remember_youtube_videos([
        VideoCandidate(VIDEO, "Portishead Roads Official Audio", "BadUploader", 305),
        VideoCandidate(VIDEO2, "Portishead Other Upload", "BadUploader", 300),
    ])


def fake_mp3(root):
    @asynccontextmanager
    async def file(video_id):
        path = root / (video_id + ".mp3")
        path.write_bytes(b"ID3" + b"\x00" * 128)
        try:
            yield path
        finally:
            path.unlink(missing_ok=True)
    return file


def test_explicit_choice_preserves_artist_and_title(tmp_path, monkeypatch):
    db = Library(tmp_path / "db.sqlite")
    setup(db)
    monkeypatch.setattr(youtube, "temporary_youtube_mp3", fake_mp3(tmp_path))
    published = []

    async def publish(path, title, artist):
        published.append((title, artist))
        return "tg-file", "tg-unique"

    async def scenario():
        svc = YouTubeIngestService(db, publish)
        imported = await svc.import_video(VIDEO, recording_mbid=MBID.upper())
        assert imported["reused"] is False
        track = db.get_track(imported["track_id"])
        assert (track.title, track.artist) == ("Roads", "Portishead")
        assert published == [("Roads", "Portishead")]
        assert db.resolved_catalog_tracks()[MBID] == track.id
        assert db.recording_for_track(track.id)["provenance"] == "import"
        repeated = await svc.import_video(VIDEO, recording_mbid=MBID)
        assert repeated == {"track_id": track.id, "reused": True}
        assert published == [("Roads", "Portishead")]

    asyncio.run(scenario())


def test_raw_youtube_import_stays_unmatched_until_explicit_choice(tmp_path, monkeypatch):
    db = Library(tmp_path / "db.sqlite")
    setup(db)
    monkeypatch.setattr(youtube, "temporary_youtube_mp3", fake_mp3(tmp_path))

    async def publish(*_):
        return "tg-file", "tg-unique"

    async def scenario():
        svc = YouTubeIngestService(db, publish)
        first = await svc.import_video(VIDEO)
        tid = first["track_id"]
        assert db.resolved_catalog_tracks() == {}
        assert db.get_track(tid).artist == "BadUploader"
        linked = await svc.import_video(VIDEO, recording_mbid=MBID)
        assert linked == {"track_id": tid, "reused": True}
        assert db.resolved_catalog_tracks()[MBID] == tid
        # A plain YouTube import may later be promoted to accurate
        # MusicBrainz metadata by explicit owner selection.
        assert db.get_track(tid).title == "Roads"
        assert db.get_track(tid).artist == "Portishead"
        with pytest.raises(YouTubeError, match="already linked"):
            await svc.import_video(VIDEO, recording_mbid=OTHER)

    asyncio.run(scenario())


def test_duplicate_recording_from_other_video_does_not_download(tmp_path, monkeypatch):
    db = Library(tmp_path / "db.sqlite")
    setup(db)
    monkeypatch.setattr(youtube, "temporary_youtube_mp3", fake_mp3(tmp_path))
    calls = []

    async def publish(path, title, artist):
        calls.append((title, artist))
        return "tg-file", "tg-unique"

    async def scenario():
        svc = YouTubeIngestService(db, publish)
        await svc.import_video(VIDEO, recording_mbid=MBID)
        with pytest.raises(YouTubeError, match="уже сохранена"):
            await svc.import_video(VIDEO2, recording_mbid=MBID)
    asyncio.run(scenario())
    assert calls == [("Roads", "Portishead")]
    assert db.get_youtube_video(VIDEO2)["track_id"] is None


def test_invalid_or_unknown_mbid_rejected_before_download(tmp_path, monkeypatch):
    db = Library(tmp_path / "db.sqlite")
    setup(db)
    monkeypatch.setattr(youtube, "temporary_youtube_mp3",
                        lambda video: (_ for _ in ()).throw(
                            AssertionError("download not expected")))
    async def forbidden(*_):
        raise AssertionError("no upload expected")
    async def scenario():
        for mbid in ("unknown-id", "00000000-0000-0000-0000-000000000000"):
            with pytest.raises(YouTubeError):
                await YouTubeIngestService(db, forbidden).import_video(
                    VIDEO, recording_mbid=mbid
                )
    asyncio.run(scenario())


def test_ytfor_buttons_carry_explicit_recording_within_64_bytes():
    video = VideoCandidate(VIDEO, "Roads", "Portishead", 300)
    without = youtube_keyboard([video], True)
    assert without.inline_keyboard[0][0].callback_data == f"ytget:{VIDEO}"
    with_mbid = youtube_keyboard([video], True, MBID)
    payload = with_mbid.inline_keyboard[0][0].callback_data
    assert payload == f"ytget:{VIDEO}:{MBID}"
    assert len(payload.encode()) <= 64
    assert youtube_keyboard([video], False, MBID) is None
