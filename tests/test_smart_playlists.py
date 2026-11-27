"""Smart collections are live library queries, not duplicated playlists."""
import pytest

from music_bot.main import create_router, home_keyboard
from music_bot.smart_playlists import smart_counts, smart_page, smart_tracks
from music_bot.storage import Library
from music_bot.telegram_smart import create_smart_queue, smart_detail, smart_home


def test_smart_rules_follow_current_library_and_skip_parked_tracks(tmp_path):
    db = Library(tmp_path / "music.db")
    recent, old, favorite, rated, rejected, unknown = [
        db.add_track(f"f{i}", f"u{i}", f"Song {i}", "Artist") for i in range(6)]
    db.like(favorite.id)
    db.set_rating(rated.id, 9)
    db.set_rating(rejected.id, 10)
    db.dislike(rejected.id)
    with db.con() as con:
        con.execute("UPDATE tracks SET added_at=datetime('now','-60 days') WHERE id=?", (old.id,))
        con.execute("UPDATE tracks SET added_at=NULL WHERE id=?", (unknown.id,))
    db.record_single_send(old.id)
    def ids(key):
        return {t.id for t in smart_tracks(db, key)}
    assert ids("recent") == {recent.id, favorite.id, rated.id}
    assert ids("top") == {rated.id}
    assert ids("forgotten") == {favorite.id}
    assert ids("unrated") == {recent.id, old.id, favorite.id, unknown.id}
    assert ids("unheard") == {recent.id, favorite.id, rated.id, unknown.id}
    assert smart_counts(db) == {"recent": 3, "top": 1, "forgotten": 1, "unrated": 4, "unheard": 4}
    db.record_single_send(favorite.id)
    assert not ids("forgotten")
    db.set_rating(recent.id, 8)
    assert ids("top") == {rated.id, recent.id}
    db.set_track_verdict(recent.id, "later")
    assert ids("top") == {rated.id}
    assert db.playback_stats() == {}


def test_smart_pages_and_queue_snapshot(tmp_path):
    db = Library(tmp_path / "music.db")
    tracks = [db.add_track(f"f{i}", f"u{i}", f"Song {i}", "Artist") for i in range(12)]
    total, page, pages, rows = smart_page(db, "unrated", 1)
    assert (total, page, pages) == (12, 1, 2)
    assert [t.id for t in rows] == [t.id for t in reversed(tracks[:4])]
    qid = create_smart_queue(db, "unrated")
    db.set_rating(tracks[-1].id, 10)
    assert smart_counts(db)["unrated"] == 11
    assert db.peek_queue(qid).tracks[0].id == tracks[-1].id
    assert db.queue_snapshot(qid)["pending_total"] == 12
    _, markup = smart_detail(db, "unrated")
    callbacks = {b.callback_data for row in markup.inline_keyboard for b in row}
    assert {"smartplay:unrated:all", "smartplay:unrated:shuffle", "smart:unrated:1"} <= callbacks
    _, markup = smart_home(db)
    assert any(b.callback_data == "smart:top:0" for row in markup.inline_keyboard for b in row)
    assert any(b.callback_data == "smart:home" for row in home_keyboard().inline_keyboard for b in row)
    with pytest.raises(ValueError):
        create_smart_queue(db, "forgotten")
    with pytest.raises(ValueError):
        smart_page(db, "unknown")
    assert create_router(db, 42)
