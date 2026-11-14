"""Queue editing is atomic, future-only and safe across two processes."""
import pytest
from music_bot.storage import Library
from music_bot.selection import select_wave_batch


def tracks(db, n=15):
    return [
        db.add_track(f"file{i}",f"unique{i}",f"Song {i}",f"Artist {i%3}")
        for i in range(n)
    ]


def test_next_append_reorder_and_remove_do_not_change_sent_history(tmp_path):
    path=tmp_path/"db.sqlite"
    db=Library(path)
    saved=tracks(db, 18)
    queue=db.create_queue([t.id for t in saved[:15]],"My playlist","playlist")
    assert db.mark_queue_sent(db.peek_queue(queue))
    original_sent=db.recent_ids()
    assert db.queue_snapshot(queue)["cursor"]==10

    second=Library(path)
    state=second.edit_queue(queue,"next",track_id=saved[17].id,
                            expected_cursor=10)
    assert state["upcoming"][0]["track_id"]==saved[17].id
    assert state["upcoming"][0]["position"]==10

    state=db.edit_queue(queue,"append",track_id=saved[16].id)
    assert state["upcoming"][-1]["track_id"]==saved[16].id

    state=db.edit_queue(queue,"move",position=16,to_position=11)
    assert state["upcoming"][1]["track_id"]==saved[16].id
    state=db.edit_queue(queue,"remove",position=10)
    assert state["upcoming"][0]["track_id"]==saved[16].id
    assert db.recent_ids()==original_sent
    assert [t.id for t in db.peek_queue(queue).tracks][:2] == [
        saved[16].id, saved[10].id
    ]
    with pytest.raises(ValueError,match="already sent"):
        db.edit_queue(queue,"remove",position=0)
    with pytest.raises(ValueError,match="Queue changed"):
        db.edit_queue(queue,"next",track_id=saved[17].id,expected_cursor=0)


def test_edits_refuse_in_flight_telegram_queue(tmp_path):
    db=Library(tmp_path/"music.db")
    songs=tracks(db,12)
    q=db.create_queue([t.id for t in songs],"Road","playlist")
    lease=db.acquire_media_import("queue",str(q))
    with pytest.raises(ValueError,match="currently sending"):
        Library(db.path).edit_queue(q,"next",track_id=songs[3].id)
    assert db.release_media_import("queue",str(q),lease)
    state=db.edit_queue(q,"next",track_id=songs[3].id)
    assert state["upcoming"][0]["track_id"]==songs[3].id


def test_dynamic_filters_apply_to_new_batches_not_static_playlists(tmp_path):
    db=Library(tmp_path/"db.sqlite")
    songs=tracks(db,25)
    for t in songs[:7]:
        db.like(t.id)
        db.set_rating(t.id,9)
    for t in songs[7:12]:
        db.set_rating(t.id,7)
    queue=db.create_queue([songs[12].id],"Wave","balanced")
    filters=db.set_queue_filters(
        queue,min_rating=7,liked_only=True,no_repeat_today=False,
    )
    assert filters=={
        "min_rating":7,"liked_only":True,"no_repeat_today":False
    }
    result=select_wave_batch(db,"balanced",count=10,queue_id=queue)
    assert {t.id for t in result} <= {t.id for t in songs[:7]}
    q2=db.create_queue([songs[1].id],"Playlist","playlist")
    with pytest.raises(ValueError,match="radio"):
        db.set_queue_filters(q2,min_rating=7,liked_only=True,no_repeat_today=True)
    assert db.mark_queue_sent(db.peek_queue(queue))
    db.set_queue_filters(queue,min_rating=7,liked_only=True,no_repeat_today=True)
    again=select_wave_batch(db,"balanced",count=10,queue_id=queue)
    assert songs[12].id not in {t.id for t in again}
    for t in songs[:7]:
        db.record_single_send(t.id)
    assert select_wave_batch(db,"balanced",count=10,queue_id=queue)==[]


def test_queue_edits_validate_track_and_positions(tmp_path):
    db=Library(tmp_path/"db.sqlite")
    saved=tracks(db,4)
    q=db.create_queue([t.id for t in saved[:2]],"Test","playlist")
    for action in ("delete_all","rename"):
        with pytest.raises(ValueError):
            db.edit_queue(q,action)
    with pytest.raises(ValueError):
        db.edit_queue(q,"next",track_id=999999)
    with pytest.raises(ValueError):
        db.edit_queue(q,"move",position=0,to_position=9)
    with pytest.raises(ValueError):
        db.edit_queue(q,"remove",position=-1)
    assert db.queue_snapshot(q)["pending_total"]==2
