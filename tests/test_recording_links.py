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
