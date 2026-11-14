import sqlite3
import pytest
from music_bot.storage import Library
from music_bot.recommend import wave

@pytest.fixture
def lib(tmp_path):
    return Library(tmp_path / "music.db")

def test_liked_and_playlists(lib):
    t = lib.add_track("file-id", "unique-id", "Song", "Band")
    assert lib.like(t.id)
    assert not lib.like(t.id)
    assert lib.liked_ids() == {t.id}
    pid = lib.create_playlist("Workout")
    assert lib.add_to(pid, t.id)
    assert not lib.add_to(pid, t.id)
    assert [x.id for x in lib.playlist_tracks(pid)] == [t.id]
    with pytest.raises(sqlite3.IntegrityError):
        lib.create_playlist("workout")

def test_dedup_and_dislikes(lib):
    t1 = lib.add_track("old-id", "same-unique", "Song", "Band")
    t2 = lib.add_track("new-id", "same-unique", "Song 2", "Band")
    assert t1.id == t2.id and t2.file_id == "new-id"
    lib.dislike(t1.id)
    assert lib.disliked_ids() == {t1.id}
    assert wave(lib.all_tracks(), lib.liked_ids(), lib.disliked_ids()) == []

def test_wave_deterministic(lib):
    for i in range(7):
        lib.add_track(f"id-{i}", f"unique-{i}", f"Song {i}", f"Artist {i % 2}")
    ts = lib.all_tracks()
    a = wave(ts, set(), count=5, seed=42)
    b = wave(ts, set(), count=5, seed=42)
    assert [t.id for t in a] == [t.id for t in b]
    assert len({t.id for t in a}) == 5
