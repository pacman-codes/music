"""Delivery history never implies native Telegram playback."""
import pytest

from music_bot.main import create_router, home_keyboard
from music_bot.storage import Library
from music_bot.telegram_history import history_page, replay_delivery_queue


def test_history_is_unique_and_replay_uses_only_delivered_items(tmp_path):
    db = Library(tmp_path / "music.db")
    tracks = [db.add_track(f"f{i}", f"u{i}", f"Track {i}", "Artist") for i in range(13)]
    qid = db.create_queue([t.id for t in tracks], "Original", "playlist")
    db.mark_queue_sent(db.peek_queue(qid))
    db.record_single_send(tracks[0].id)
    assert db.playback_stats() == {}
    first = db.recent_deliveries(limit=3)
    assert [r["track_id"] for r in first] == [tracks[0].id, tracks[9].id, tracks[8].id]
    second = db.recent_deliveries(before=first[-1]["delivery_id"], limit=20)
    assert tracks[0].id not in [r["track_id"] for r in second]
    assert len(second) == 7
    queues = db.recent_delivered_queues()
    assert len(queues) == 1 and queues[0]["delivered_count"] == 10
    replay = replay_delivery_queue(db, qid)
    assert replay != qid
    assert [t.id for t in db.peek_queue(replay).tracks] == [t.id for t in tracks[:10]]
    assert db.queue_snapshot(qid)["pending_total"] == 3
    assert db.queue_snapshot(replay)["pending_total"] == 10
    assert len(db.recent_delivered_queues()) == 1
    with pytest.raises(ValueError):
        replay_delivery_queue(db, replay)


def test_history_replay_preserves_duplicates_and_pages(tmp_path):
    db = Library(tmp_path / "music.db")
    t = db.add_track("f", "u", "Title", "Artist")
    qid = db.create_queue([t.id, t.id], "Repeat", "playlist", preserve_duplicates=True)
    db.mark_queue_sent(db.peek_queue(qid))
    replay = replay_delivery_queue(db, qid)
    assert [v.id for v in db.peek_queue(replay).tracks] == [t.id, t.id]
    for i in range(10):
        song = db.add_track(f"f{i}", f"u{i}", f"Song {i}", "Artist")
        db.record_single_send(song.id)
    text, markup = history_page(db)
    assert "не факт прослушивания" in text
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert any(v.startswith("hist:tracks:") and v != "hist:tracks:0" for v in callbacks)
    text, markup = history_page(db, "queues")
    assert "2 отправлено" in text
    assert any(b.callback_data == f"histreplay:{qid}" for row in markup.inline_keyboard for b in row)
    assert any(b.callback_data == "hist:tracks:0" for row in home_keyboard().inline_keyboard for b in row)
    assert create_router(db, 42)
