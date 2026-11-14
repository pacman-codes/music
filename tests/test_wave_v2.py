"""Wave 2.0 quota mixing uses ONLY real Telegram audio assets."""
from collections import Counter

import pytest
from music_bot.storage import Library
from music_bot.selection import select_wave_batch
from music_bot.wave_v2 import PRESETS, allocate_mix, wave_v2, classify


def build(db):
    tracks=[]
    for i in range(10):
        tracks.append(db.add_track("f"+str(i),"u"+str(i),
                                   "Familiar "+str(i),"Seed Artist"))
        db.like(tracks[-1].id)
        db.set_rating(tracks[-1].id,9)
    for i in range(10):
        tracks.append(db.add_track("s"+str(i),"us"+str(i),
                                   "Similar "+str(i),"Seed Artist"))
    for i in range(20):
        tracks.append(db.add_track("n"+str(i),"un"+str(i),
                                   "New "+str(i),"New Artist "+str(i%6)))
    return tracks


def test_quota_allocation_and_exact_balanced_mix(tmp_path):
    db=Library(tmp_path/"m.db")
    tracks=build(db)
    assert allocate_mix(10,{"familiar":10,"similar":10,"new":20},"balanced")=={
        "familiar":6,"similar":3,"new":1
    }
    result=wave_v2(tracks,db.liked_ids(),ratings=db.ratings(),count=10,
                   preset="balanced",seed=15)
    types=classify(tracks,db.liked_ids(),db.ratings(),{})
    by_id={t.id:name for name,ts in types.items() for t in ts}
    assert Counter(by_id[t.id] for t in result)=={
        "familiar":6,"similar":3,"new":1
    }
    assert len({t.id for t in result})==10
    assert all(t.file_id for t in result)


def test_preset_and_category_shortages_fall_back_without_dead_end(tmp_path):
    db=Library(tmp_path/"m.db")
    tracks=build(db)
    assert allocate_mix(10,{"familiar":2,"similar":2,"new":20},"balanced")=={
        "familiar":2,"similar":2,"new":6
    }
    no_likes=wave_v2(tracks,set(),count=10,seed=2)
    assert len(no_likes)==10
    for preset in PRESETS:
        assert len(wave_v2(tracks,db.liked_ids(),ratings=db.ratings(),
                           count=10,preset=preset,seed=2))==10
    with pytest.raises(ValueError):
        wave_v2(tracks,set(),preset="invalid")


def test_skipped_favorites_remain_liked_but_leave_wave(tmp_path):
    db=Library(tmp_path/"m.db")
    tracks=build(db)
    exhausted=tracks[0]
    for _ in range(3):
        db.record_playback_event(exhausted.id,"skip")
    result=wave_v2(tracks,db.liked_ids(),ratings=db.ratings(),
                   playback=db.playback_stats(),count=30,seed=7)
    assert exhausted.id not in {t.id for t in result}
    assert exhausted.id in db.liked_ids()


def test_preset_persists_and_changes_live_selection(tmp_path):
    path=tmp_path/"m.db"
    db=Library(path)
    tracks=build(db)
    assert db.get_wave_preset()=="balanced"
    db.set_wave_preset("explore")
    assert Library(path).get_wave_preset()=="explore"
    from music_bot.selection import select_wave_batch
    result=select_wave_batch(db,"balanced",count=10)
    groups=classify(tracks,db.liked_ids(),db.ratings(),{})
    by_id={t.id:name for name,ts in groups.items() for t in ts}
    assert Counter(by_id[t.id] for t in result)=={
        "familiar":3,"similar":3,"new":4
    }
    with pytest.raises(ValueError):
        db.set_wave_preset("delete_db")
