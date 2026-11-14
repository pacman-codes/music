"""Recording identity is owner-confirmed, never inferred from text similarity."""
import sqlite3

import pytest

from music_bot.catalog import CatalogTrack
from music_bot.storage import Library

A="8a49dba0-253a-4535-b87f-78bb035336ce"
B="f3bba4cd-8018-468b-902e-bc8f029593e5"
ARTIST="8f6bd1e4-fbe1-4f50-aa9b-94c450ec0f11"


@pytest.fixture
def db(tmp_path):
    return Library(tmp_path / "music.db")


def candidates(db):
    db.remember_candidates([
        CatalogTrack(A, "Roads", "Portishead", artist_mbid=ARTIST),
        CatalogTrack(B, "Roads (Live)", "Portishead", artist_mbid=ARTIST, variant=True),
    ])


def test_explicit_track_match_not_automatically_inferred(db):
    track = db.add_track("file", "unique", "Roads", "Portishead")
    candidates(db)
    assert db.recording_for_track(track.id) is None
    assert db.resolved_catalog_tracks() == {}
    match = db.confirm_recording_match(track.id, A)
    assert match["title"] == "Roads"
    assert db.recording_for_track(track.id)["mbid"] == A
    assert db.recording_for_track(track.id)["artist_mbid"] == ARTIST
    assert db.resolved_catalog_tracks() == {A:track.id}
    # User can correct a mistaken manual match.
    db.confirm_recording_match(track.id, B)
    assert db.recording_for_track(track.id)["mbid"] == B
    assert db.unlink_recording_match(track.id) is True
    assert db.recording_for_track(track.id) is None
    assert db.unlink_recording_match(track.id) is False


def test_cannot_match_to_unknown_mbid_or_nonexistent_telegram_track(db):
    track = db.add_track("file", "unique", "Title", "Artist")
    candidates(db)
    with pytest.raises(ValueError):
        db.confirm_recording_match(track.id, "00000000-0000-0000-0000-000000000000")
    with pytest.raises(ValueError):
        db.confirm_recording_match(99999, A)
    assert db.recording_for_tracks() == {}


def test_imported_link_is_immutable_for_manual_relink(db):
    track=db.add_track("file", "unique", "Roads", "Portishead")
    candidates(db)
    db.link_candidate_track(A, track.id)
    assert db.recording_for_track(track.id)["provenance"] == "import"
    with pytest.raises(ValueError):
        db.confirm_recording_match(track.id, B)
    assert db.unlink_recording_match(track.id) is False
    assert db.resolved_catalog_tracks()[A] == track.id


def test_migrates_older_imported_assets_on_reopen(tmp_path):
    path=tmp_path / "music.db"
    db=Library(path)
    candidates(db)
    t=db.add_track("file","uid","Roads","Portishead")
    # Old databases stored only catalog_audio_assets, without identity links.
    with db.con() as conn:
        conn.execute("INSERT INTO catalog_audio_assets(mbid,track_id) VALUES(?,?)",
                     (A,t.id))
    migrated=Library(path)
    assert migrated.recording_for_track(t.id)["provenance"] == "import"
    assert migrated.resolved_catalog_tracks()[A] == t.id
    # Reopening repeatedly is idempotent.
    assert Library(path).recording_for_track(t.id)["mbid"] == A


def test_link_removed_when_track_deleted(db):
    t=db.add_track("file", "uid", "Roads", "Portishead")
    candidates(db)
    db.confirm_recording_match(t.id, A)
    with db.con() as conn:
        conn.execute("DELETE FROM tracks WHERE id=?", (t.id,))
    assert db.recording_for_track(t.id) is None
    assert db.resolved_catalog_tracks() == {}


def test_imported_recording_identity_never_silently_reassigned(db):
    candidates(db)
    first = db.add_track("tg-first", "same-audio", "Roads", "Portishead")
    second = db.add_track("tg-second", "different-audio", "Roads", "Portishead")
    db.link_candidate_track(A, first.id)
    db.link_candidate_track(A, first.id)  # same link is idempotent
    with pytest.raises(ValueError, match="another recording"):
        db.link_candidate_track(B, first.id)
    with pytest.raises(ValueError, match="different Telegram audio"):
        db.link_candidate_track(A, second.id)
    assert db.resolved_catalog_tracks() == {A: first.id}
    assert db.recording_for_track(first.id)["mbid"] == A
    assert db.recording_for_track(second.id) is None
    assert db.get_candidate(B)["track_id"] is None


def test_direct_import_duplicate_unique_file_keeps_original_identity(tmp_path):
    import asyncio
    import httpx

    from music_bot.importer import AudioIngestService, MediaImportError

    db = Library(tmp_path / "music.db")
    candidates(db)

    def data(_request):
        return httpx.Response(
            200, headers={"content-type": "audio/mpeg"},
            content=b"ID3" + b"\x00" * 200,
        )

    async def same_telegram_file(_path, _title, _artist):
        # Telegram returns the same unique-file ID for equal media bytes.
        return "existing-telegram-id", "same-audio-id"

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(data)) as http:
            service = AudioIngestService(
                db, ("audio.example.org",), same_telegram_file, client=http,
            )
            url = "https://audio.example.org/music.mp3"
            first = await service.import_recording(A, url)
            assert first["track_id"] in db.linked_candidate_tracks().values()
            with pytest.raises(MediaImportError, match="уже связан"):
                await service.import_recording(B, url)
            assert db.recording_for_track(first["track_id"])["mbid"] == A
            assert db.get_candidate(B)["track_id"] is None

    asyncio.run(scenario())
