"""Regression tests for core findings in code audit."""
from music_bot.catalog import CatalogTrack
from music_bot.personalized import ranked_seed_artists
from music_bot.recommend import track_weight
from music_bot.selection import select_wave_batch
from music_bot.storage import Library

MBID="8a49dba0-253a-4535-b87f-78bb035336ce"

def test_old_liked_playlist_methods_reconcile_five_statuses(tmp_path):
    db=Library(tmp_path/"m.db")
    t=db.add_track("file","uniq","Music","Artist")
    db.set_track_verdict(t.id,"never")
    db.add_to(db.liked_id(),t.id)
    assert db.track_verdicts()[t.id]=="add"
    assert t.id in db.liked_ids() and t.id not in db.disliked_ids()
    db.remove_from(db.liked_id(),t.id)
    assert t.id not in db.track_verdicts()
    assert t.id not in db.liked_ids()
    db.dislike(t.id)
    assert db.track_verdicts()[t.id]=="never"
    db.like(t.id)
    assert db.track_verdicts()[t.id]=="add"

def test_previously_rated_catalog_song_follows_late_audio_match(tmp_path):
    db=Library(tmp_path/"m.db")
    db.remember_candidates([CatalogTrack(MBID,"Crowded Roomz","Nia Archives")])
    db.set_catalog_verdict(MBID,"add")
    t=db.add_track("file","uid","Local","Unknown")
    db.confirm_recording_match(t.id,MBID)
    assert t.id in db.liked_ids()
    assert db.track_verdicts()[t.id]=="add"
    assert len(db.verdict_items("add"))==1
    db.set_catalog_verdict(MBID,"later")
    assert db.track_verdicts()[t.id]=="later"
    assert t.id not in db.liked_ids()

def test_local_verdict_wins_same_second_link_collision(tmp_path):
    db=Library(tmp_path/"m.db")
    db.remember_candidates([CatalogTrack(MBID,"Song","Artist")])
    db.set_catalog_verdict(MBID,"never")
    t=db.add_track("file","uid","Song","Artist")
    db.set_track_verdict(t.id,"interesting")
    db.confirm_recording_match(t.id,MBID)
    assert db.catalog_verdicts()[MBID]=="interesting"
    assert t.id not in db.disliked_ids()

def test_liked_ids_are_not_limited_to_a_thousand(tmp_path):
    db=Library(tmp_path/"m.db")
    with db.con() as sql:
        sql.executemany(
            "INSERT INTO tracks(id,file_id,file_unique_id,title,artist) VALUES(?,?,?,?,?)",
            [(i,f"f{i}",f"u{i}",f"T{i}","Artist") for i in range(1,1011)])
        liked=db.liked_id()
        sql.executemany(
            "INSERT INTO playlist_tracks(playlist_id,track_id,position) VALUES(?,?,?)",
            [(liked,i,i) for i in range(1,1011)])
    assert len(db.liked_ids())==1010

def test_verdict_counter_and_pagination_work_after_10000(tmp_path):
    db=Library(tmp_path/"m.db")
    with db.con() as sql:
        sql.executemany(
            "INSERT INTO catalog_candidates(mbid,title,artist) VALUES(?,?,?)",
            [(f"m{i:035d}",f"T{i}","A") for i in range(10012)])
        sql.execute(
            "INSERT INTO catalog_verdicts(mbid,status) SELECT mbid,'interesting' FROM catalog_candidates")
    assert db.verdict_counts()["interesting"]==10012
    assert len(db.verdict_items("interesting",limit=8,offset=10008))==4
    assert db.verdict_playable_ids("interesting")==[]

def test_queue_prunes_only_automatic_pending(tmp_path):
    db=Library(tmp_path/"m.db")
    t=[db.add_track(f"f{i}",f"u{i}",f"T{i}","Artist") for i in range(3)]
    auto=db.create_queue([x.id for x in t],"Radio","balanced")
    manual=db.create_queue([x.id for x in t],"Playlist","playlist")
    db.set_track_verdict(t[1].id,"never")
    db.set_track_verdict(t[2].id,"later")
    assert db.prune_automatic_pending(auto)==2
    assert [x.id for x in db.peek_queue(auto).tracks]==[t[0].id]
    assert db.prune_automatic_pending(manual)==0
    assert len(db.peek_queue(manual).tracks)==3

def test_cyrillic_search_is_casefolded(tmp_path):
    db=Library(tmp_path/"m.db")
    t=db.add_track("f","u","Всё в порядке","Нервы")
    assert [x.id for x in db.search_tracks("нервы")]==[t.id]
    assert [x.id for x in db.search_tracks("ВСЁ")]==[t.id]

def test_catalog_only_add_is_seed_and_later_is_not(tmp_path):
    db=Library(tmp_path/"m.db")
    t=db.add_track("f","u","Music","Other")
    db.set_rating(t.id,10)
    db.set_track_verdict(t.id,"interesting")
    assert ranked_seed_artists(db)==[]
    db.remember_candidates([CatalogTrack(MBID,"Crowded Roomz","Nia Archives")])
    db.set_catalog_verdict(MBID,"add")
    assert ranked_seed_artists(db)[0].name=="Nia Archives"
    assert select_wave_batch(db,"balanced")==[]

def test_maybe_gets_small_positive_weight_not_a_like(tmp_path):
    db=Library(tmp_path/"m.db")
    t=db.add_track("f","u","Maybe","Artist")
    db.set_track_verdict(t.id,"maybe")
    assert t.id not in db.liked_ids()
    assert ranked_seed_artists(db)[0].name=="Artist"
    assert track_weight(t,set(),{}, {},maybe_ids={t.id}) > track_weight(t,set(),{},{})
    assert [x.id for x in select_wave_batch(db,"balanced")]==[t.id]



def test_upgrade_reconciles_existing_catalog_only_decision_on_link(tmp_path):
    db=Library(tmp_path/"m.db")
    db.remember_candidates([CatalogTrack(MBID,"Song","Artist")])
    t=db.add_track("f","u","Song","Artist")
    db.confirm_recording_match(t.id,MBID)
    with db.con() as sql:
        sql.execute(
            "INSERT INTO catalog_verdicts(mbid,status) VALUES(?,'add')",
            (MBID,),
        )
    upgraded=Library(db.path)
    assert upgraded.track_verdicts()[t.id]=="add"
    assert t.id in upgraded.liked_ids()


def test_youtube_cannot_claim_recording_confirmed_for_other_audio(tmp_path):
    import pytest
    from music_bot.youtube import VideoCandidate
    db=Library(tmp_path/"m.db")
    db.remember_candidates([CatalogTrack(MBID,"Song","Artist")])
    first=db.add_track("f1","u1","Song","Artist")
    second=db.add_track("f2","u2","Video","Channel")
    db.confirm_recording_match(first.id,MBID)
    video=VideoCandidate("12345678901","Video","Channel",120)
    db.remember_youtube_videos([video])
    db.link_youtube_video(video.video_id,second.id)
    with pytest.raises(ValueError):
        db.associate_youtube_recording(video.video_id,MBID)
    assert db.recording_for_track(first.id)["mbid"]==MBID
    assert db.recording_for_track(second.id) is None

