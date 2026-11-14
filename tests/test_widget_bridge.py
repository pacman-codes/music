"""Unit tests for future widget transport: no Telegram API/network required."""
import sqlite3

from music_bot.storage import Library
from music_bot.widget_bridge import handle_widget_query
from music_bot.widgets import parse_rating_request


def request(qid="q1", action="rate:9", *, user=42, chat=42,
            chat_type="private", message=100):
    return {"update_id": 1, "widget_query": {
        "id": qid, "from": {"id": user},
        "chat": {"id": chat, "type": chat_type},
        "message_id": message, "data": action,
    }}


def test_trusted_ratings_and_manual_events_are_idempotent(tmp_path):
    db = Library(tmp_path / "music.db")
    track = db.add_track("f1", "uid1", "Title", "Band")
    db.bind_widget(42, 100, track.id, now=1000, lifetime=2000000000)
    once = handle_widget_query(request("q1", "rate:9"), owner_id=42, db=db)
    again = handle_widget_query(request("q1", "rate:9"), owner_id=42, db=db)
    assert once == again and once.data == "Оценка 9/10 сохранена"
    assert db.ratings() == {track.id: 9}
    handle_widget_query(request("q2", "skip"), owner_id=42, db=db)
    handle_widget_query(request("q2", "skip"), owner_id=42, db=db)
    handle_widget_query(request("q3", "complete"), owner_id=42, db=db)
    assert db.playback_stats() == {track.id: (1, 1)}
    with db.con() as con:
        assert con.execute("SELECT COUNT(*) FROM widget_receipts").fetchone()[0] == 3
        assert con.execute("SELECT COUNT(*) FROM playback_events").fetchone()[0] == 2


def test_reject_other_chat_user_or_unknown_message(tmp_path):
    db = Library(tmp_path / "music.db")
    track = db.add_track("f", "uid", "Track", "Band")
    db.bind_widget(42, 100, track.id)
    for update in [
        request("a", user=44),
        request("b", chat=43),
        request("c", chat_type="group"),
        request("d", message=101),
        request("e", action="rate:20"),
        {**request("f"), "widget_query": {**request("f")["widget_query"], "inline_message_id": "foo"}},
        {**request("g"), "widget_query": {**request("g")["widget_query"], "widget_id": "share"}},
    ]:
        result = handle_widget_query(update, owner_id=42, db=db)
        assert result is not None and result.data != "Оценка 9/10 сохранена"
    assert db.ratings() == {}
    assert db.playback_stats() == {}


def test_expired_binding_never_modifies_rating(tmp_path):
    db = Library(tmp_path / "music.db")
    t = db.add_track("f", "uid", "Song", "Artist")
    db.bind_widget(42, 100, t.id, now=100, lifetime=2)
    result = handle_widget_query(request(), owner_id=42, db=db)
    assert "устарела" in result.data
    assert db.ratings() == {}


def test_widget_binding_survives_reopen(tmp_path):
    path = tmp_path / "music.db"
    db = Library(path)
    track = db.add_track("f", "uid", "Title", "Artist")
    db.bind_widget(42, 100, track.id)
    refreshed = Library(path)
    assert handle_widget_query(request(), owner_id=42, db=refreshed).data.startswith("Оценка")
    assert refreshed.ratings()[track.id] == 9
