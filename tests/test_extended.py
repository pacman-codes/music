import sqlite3

import pytest

from music_bot.storage import Library
from music_bot.recommend import wave


@pytest.fixture
def lib(tmp_path):
    return Library(tmp_path / "music.db")


def seed(lib, count=25):
    return [
        lib.add_track(f"file-{i}", f"unique-{i}", f"Title {i}", f"Artist {i % 4}")
        for i in range(count)
    ]


def test_playlist_edit_reorder_and_delete(lib):
    a, b, c = seed(lib, 3)
    p = lib.create_playlist("Road")
    for t in (a, b, c):
        lib.add_to(p, t.id)
    lib.move_track(p, c.id, 1)
    assert [t.id for t in lib.playlist_tracks(p)] == [c.id, a.id, b.id]
    lib.remove_from(p, a.id)
    assert [t.id for t in lib.playlist_tracks(p)] == [c.id, b.id]
    lib.rename_playlist(p, "Running")
    assert lib.get_playlist(p)["name"] == "Running"
    lib.delete_playlist(p)
    assert lib.get_playlist(p) is None
    with pytest.raises(ValueError):
        lib.delete_playlist(lib.liked_id())


def test_like_and_dislike_cannot_conflict(lib):
    a = seed(lib, 1)[0]
    lib.like(a.id)
    lib.dislike(a.id)
    assert a.id not in lib.liked_ids()
    assert a.id in lib.disliked_ids()
    lib.like(a.id)
    assert a.id not in lib.disliked_ids()


def test_search_literal_and_playlist_unique(lib):
    a = lib.add_track("f", "u", "100% Real", "Band")
    assert lib.search_tracks("%")[0].id == a.id
    assert lib.search_tracks("missing") == []
    lib.create_playlist("Gym")
    with pytest.raises(sqlite3.IntegrityError):
        lib.rename_playlist(lib.create_playlist("Sleep"), "gym")


def test_queue_pagination_and_history(lib):
    tracks = seed(lib, 25)
    q = lib.create_queue([t.id for t in tracks], "Radio", "wave")
    first = lib.peek_queue(q)
    assert len(first.tracks) == 10 and first.remaining_after == 15
    assert lib.mark_queue_sent(first)
    assert not lib.mark_queue_sent(first)
    second = lib.peek_queue(q)
    assert [t.id for t in second.tracks] == [t.id for t in tracks[10:20]]
    assert lib.mark_queue_sent(second)
    third = lib.peek_queue(q)
    assert len(third.tracks) == 5 and third.remaining_after == 0
    assert len(lib.recent_ids()) == 20
    assert lib.mark_queue_sent(third)
    assert not lib.peek_queue(q).tracks


def test_wave_modes_and_history_penalty(lib):
    tracks = seed(lib, 14)
    liked = {tracks[0].id, tracks[1].id}
    a = wave(tracks, liked, count=10, seed=7)
    b = wave(tracks, liked, count=10, seed=7)
    assert [t.id for t in a] == [t.id for t in b]
    discover = wave(tracks, liked, mode="discover", count=99, seed=1)
    assert all(t.id not in liked for t in discover)
    favorites = wave(tracks, liked, mode="favorites", count=99, seed=1)
    assert {t.id for t in favorites} == liked
    with pytest.raises(ValueError):
        wave(tracks, liked, mode="wrong")


def test_upgrade_v01_existing_database(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
          CREATE TABLE tracks(id INTEGER PRIMARY KEY,file_id TEXT NOT NULL,
            file_unique_id TEXT NOT NULL UNIQUE,title TEXT NOT NULL,artist TEXT NOT NULL);
          CREATE TABLE playlists(id INTEGER PRIMARY KEY,name TEXT UNIQUE COLLATE NOCASE,
            kind TEXT DEFAULT 'custom');
          CREATE TABLE playlist_tracks(playlist_id INTEGER,track_id INTEGER,position INTEGER,
            PRIMARY KEY(playlist_id,track_id));
          CREATE TABLE dislikes(track_id INTEGER PRIMARY KEY);
          INSERT INTO tracks VALUES(1,'f','u','Original','Artist');
          INSERT INTO playlists VALUES(1,'Мне нравится','liked');
          INSERT INTO playlist_tracks VALUES(1,1,1);
        """)
    db = Library(path)
    assert db.liked_ids() == {1}
    assert db.create_queue([1], "Migrated", "liked") > 0
