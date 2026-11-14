"""Every database operation closes its connection and rolls back on exceptions."""
import sqlite3

import pytest

from music_bot.storage import Library


def test_connections_are_closed_on_normal_and_error_paths(tmp_path):
    db=Library(tmp_path/"music.db")
    with db.con() as connection:
        assert connection.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 0
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")

    with pytest.raises(RuntimeError):
        with db.con() as connection:
            connection.execute(
                "INSERT INTO tracks(file_id,file_unique_id,title,artist) "
                "VALUES('f','u','T','A')"
            )
            raise RuntimeError("roll back")
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
    assert db.all_tracks() == []
