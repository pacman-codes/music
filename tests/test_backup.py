import sqlite3

import pytest

from music_bot.backup import backup_db
from music_bot.storage import Library


def test_backup_is_complete_and_separate(tmp_path):
    library = Library(tmp_path / "data" / "music.db")
    track = library.add_track("file", "unique", "Original", "Band")
    library.like(track.id)
    result = backup_db(library.path, tmp_path / "backups")
    assert result.exists()
    assert result.stat().st_size > 0
    with sqlite3.connect(result) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup.execute("SELECT title FROM tracks").fetchone()[0] == "Original"
        assert backup.execute("SELECT COUNT(*) FROM playlist_tracks").fetchone()[0] == 1
    # Live DB changes don't affect the saved backup.
    library.add_track("file2", "unique2", "Later", "Band")
    with sqlite3.connect(result) as backup:
        assert backup.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 1


def test_backup_never_creates_missing_source(tmp_path):
    source = tmp_path / "does-not-exist.db"
    with pytest.raises(FileNotFoundError):
        backup_db(source, tmp_path / "backups")
    assert not source.exists()
