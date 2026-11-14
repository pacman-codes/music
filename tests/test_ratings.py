"""Explicit ratings and playback provenance, never inferred from Telegram sends."""
import sqlite3
from collections import Counter
import pytest
from music_bot.recommend import fatigue_suppressed, track_weight, wave
from music_bot.storage import Library

@pytest.fixture
def db(tmp_path):
    return Library(tmp_path / "music.db")

def tracks(db,n=3):
    return [db.add_track(f"f{i}",f"u{i}",f"Song {i}",f"Artist {i%2}") for i in range(n)]

def test_rating_independent_from_favorites(db):
    t=tracks(db,1)[0]
    assert db.like(t.id)
    db.set_rating(t.id,2)
    assert db.liked_ids()=={t.id} and db.ratings()=={t.id:2}
    db.set_rating(t.id,10)
    assert db.ratings()=={t.id:10}
    assert db.unlike(t.id)
    assert db.ratings()=={t.id:10}
    assert db.clear_rating(t.id)
    for bad in (0,11,-1,4.5,True,"7"):
        with pytest.raises(ValueError): db.set_rating(t.id,bad)
    with pytest.raises(ValueError): db.set_rating(9999,7)

def test_history_is_not_playback_and_events_are_idempotent(db):
    a=tracks(db,1)[0]
    q=db.create_queue([a.id],"Wave","wave")
    assert db.mark_queue_sent(db.peek_queue(q))
    assert db.playback_stats()=={}
    assert db.record_playback_event(a.id,"skip",dedupe_key="callback-1")
    assert not db.record_playback_event(a.id,"skip",dedupe_key="callback-1")
    assert db.record_playback_event(a.id,"complete",dedupe_key="callback-2")
    assert db.playback_stats()[a.id]==(1,1)
    for bad in ("pause","send","started"):
        with pytest.raises(ValueError): db.record_playback_event(a.id,bad)
    with pytest.raises(ValueError):
        db.record_playback_event(a.id,"skip",source="telegram_native")

def test_skipped_favorite_stays_favorite_but_leaves_wave(db):
    favorite, other=tracks(db,2)
    db.like(favorite.id)
    db.set_rating(favorite.id,10)
    for i in range(3): db.record_playback_event(favorite.id,"skip",dedupe_key=f"k{i}")
    assert db.liked_ids()=={favorite.id}
    assert db.disliked_ids()==set()
    assert [t.id for t in wave(db.all_tracks(),db.liked_ids(),db.disliked_ids(),
       ratings=db.ratings(),playback=db.playback_stats(),count=20,seed=2)]==[other.id]
    db.record_playback_event(favorite.id,"complete")
    result=wave(db.all_tracks(),db.liked_ids(),db.disliked_ids(),
       ratings=db.ratings(),playback=db.playback_stats(),count=20,seed=2)
    assert {t.id for t in result}=={favorite.id,other.id}
    assert db.ratings()[favorite.id]==10

def test_completion_boost_and_rating_weights(db):
    t=tracks(db,1)[0]
    options=dict(liked_ids={t.id},favorite_artists=Counter(),seen_artist=Counter())
    base=track_weight(t,**options)
    assert track_weight(t,**options,ratings={t.id:1})<base
    assert track_weight(t,**options,ratings={t.id:10})>base
    assert track_weight(t,**options,playback={t.id:(2,0)})<base
    assert track_weight(t,**options,playback={t.id:(0,6)})>base
    assert fatigue_suppressed(3,0) and not fatigue_suppressed(3,1)

def test_last_twenty_and_thirty_day_expiry(db):
    t=tracks(db,1)[0]
    for _ in range(24): db.record_playback_event(t.id,"skip")
    db.record_playback_event(t.id,"complete")
    assert db.playback_stats()[t.id]==(19,1)
    with db.con() as con:
        con.execute("UPDATE playback_events SET created_at=datetime('now','-32 days')")
    assert db.playback_stats()=={}

def test_upgrade_old_schema(tmp_path):
    path=tmp_path/"legacy.sqlite"
    with sqlite3.connect(path) as con:
        con.executescript("""
        CREATE TABLE tracks(id INTEGER PRIMARY KEY,file_id TEXT NOT NULL,
          file_unique_id TEXT NOT NULL UNIQUE,title TEXT NOT NULL,artist TEXT NOT NULL);
        CREATE TABLE playlists(id INTEGER PRIMARY KEY,name TEXT UNIQUE COLLATE NOCASE,
          kind TEXT DEFAULT 'custom');
        CREATE TABLE playlist_tracks(playlist_id INTEGER,track_id INTEGER,position INTEGER,
          PRIMARY KEY(playlist_id,track_id));
        CREATE TABLE dislikes(track_id INTEGER PRIMARY KEY);
        INSERT INTO tracks VALUES(1,'f','u','Old','Band');
        INSERT INTO playlists VALUES(1,'Мне нравится','liked');
        INSERT INTO playlist_tracks VALUES(1,1,1);
        """)
    upgraded=Library(path)
    assert upgraded.liked_ids()=={1}
    upgraded.set_rating(1,8)
    upgraded.record_playback_event(1,"complete")
    assert upgraded.ratings()=={1:8}
    assert upgraded.playback_stats()=={1:(0,1)}
