"""Real library profile export and private-snapshot diff tests."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone, timedelta

import pytest

from music_bot.profile_export import (
    compare_profiles, export_profile, profile_json_bytes, write_private_export,
)
from music_bot.storage import Library


def test_complete_profile_exports_preferences_and_order_without_secrets(tmp_path):
    db = Library(tmp_path / "music.sqlite")
    a = db.add_track("SECRET_FILE_ID_A", "SECRET_UNIQUE_A", "Roads", "Portishead")
    b = db.add_track("SECRET_FILE_ID_B", "SECRET_UNIQUE_B", "Angel", "Massive Attack")
    db.like(a.id)
    db.set_rating(a.id, 10)
    db.set_rating(b.id, 7)
    custom = db.create_playlist("Night music")
    db.add_to(custom, b.id)
    db.add_to(custom, a.id)
    db.record_single_send(a.id)
    db.record_single_send(a.id)
    db.record_playback_event(b.id, "skip", source="manual")

    result = export_profile(db, now=datetime.now(timezone.utc))
    assert result["schema_version"] == 1
    assert result["total_tracks"] == 2
    assert {x["id"] for x in result["tracks"]} == {a.id, b.id}
    assert result["recently_added_track_ids"] == [a.id, b.id]
    assert result["unknown_addition_dates"] == 0
    assert next(x for x in result["playlists"] if x["id"] == custom)["track_ids_in_order"] == [b.id, a.id]
    by_id = {row["id"]: row for row in result["tracks"]}
    assert by_id[a.id]["liked"] is True
    assert by_id[a.id]["rating"] == 10
    assert by_id[a.id]["recent_deliveries"] == 2
    assert by_id[b.id]["manual_skips_30d"] == 1
    assert by_id[a.id]["manual_skips_30d"] == 0
    assert result["provenance"]["recent_deliveries"].endswith("NOT verified listens")
    exported = profile_json_bytes(result)
    assert "SECRET_FILE_ID" not in exported.decode("utf-8")
    assert "SECRET_UNIQUE" not in exported.decode("utf-8")
    for secret_field in ("file_id", "file_unique_id", "chat_id", "owner_id", "BOT_TOKEN"):
        assert secret_field not in exported.decode("utf-8")
    assert json.loads(exported) == result


def test_legacy_import_dates_are_unknown_not_backfilled(tmp_path):
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as sql:
        sql.executescript("""
            CREATE TABLE tracks(
                id INTEGER PRIMARY KEY, file_id TEXT NOT NULL,
                file_unique_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL, artist TEXT NOT NULL
            );
            INSERT INTO tracks(id,file_id,file_unique_id,title,artist)
            VALUES (1,'old_file','old_uid','Old','Artist');
        """)
    db = Library(path)
    result = export_profile(db)
    assert result["tracks"][0]["added_at_utc"] is None
    assert result["unknown_addition_dates"] == 1
    assert result["recently_added_track_ids"] == []
    new = db.add_track("new_file", "new_uid", "Fresh", "Artist")
    result = export_profile(db)
    assert new.id in result["recently_added_track_ids"]
    assert result["unknown_addition_dates"] == 1


def test_reimport_updates_file_id_but_keeps_original_upload_date(tmp_path):
    db = Library(tmp_path / "music.sqlite")
    track = db.add_track("fileA", "uniqueA", "A", "B")
    with db.con() as sql:
        sql.execute("UPDATE tracks SET added_at='2025-01-01 00:00:00' WHERE id=?",
                    (track.id,))
    again = db.add_track("fileB", "uniqueA", "Changed", "Other")
    assert again.id == track.id
    result = export_profile(db)
    assert result["tracks"][0]["added_at_utc"] == "2025-01-01 00:00:00"
    assert result["recently_added_track_ids"] == []
    assert result["tracks"][0]["title"] == "A"


def test_recently_added_excludes_old_and_future_dates(tmp_path):
    db = Library(tmp_path / "music.sqlite")
    a = db.add_track("a", "ua", "A", "X")
    b = db.add_track("b", "ub", "B", "Y")
    c = db.add_track("c", "uc", "C", "Z")
    with db.con() as sql:
        sql.execute("UPDATE tracks SET added_at='2020-01-01 00:00:00' WHERE id=?",
                    (a.id,))
        sql.execute("UPDATE tracks SET added_at='2099-01-01 00:00:00' WHERE id=?",
                    (c.id,))
    now = datetime.now(timezone.utc)
    snapshot = export_profile(db, now=now, recent_days=30)
    assert snapshot["recently_added_track_ids"] == [b.id]
    with pytest.raises(ValueError):
        export_profile(db, now=now.replace(tzinfo=None))
    with pytest.raises(ValueError):
        export_profile(db, recent_days=0)


def test_compare_profiles_detects_adds_and_changed_preferences(tmp_path):
    db = Library(tmp_path / "music.sqlite")
    a = db.add_track("one", "u1", "A", "Artist")
    previous = export_profile(db)
    b = db.add_track("two", "u2", "B", "Other Artist")
    db.set_rating(a.id, 9)
    db.like(a.id)
    current = export_profile(db)
    delta = compare_profiles(previous, current)
    assert [t["id"] for t in delta["new_tracks"]] == [b.id]
    assert delta["rating_changes"] == [{"id": a.id, "before": None, "after": 9}]
    assert delta["newly_liked_track_ids"] == [a.id]
    assert delta["removed_track_ids"] == []
    with pytest.raises(ValueError):
        compare_profiles({"schema_version": 2}, current)


def test_private_file_creation_exclusive_permissions(tmp_path):
    dest = tmp_path / "profile-exports" / "music-profile.json"
    data = b'{"scope": "complete_music_library"}'
    assert write_private_export(dest, data) == dest
    assert dest.read_bytes() == data
    if os.name == "posix":
        assert dest.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        write_private_export(dest, b"new bytes")
    assert dest.read_bytes() == data
