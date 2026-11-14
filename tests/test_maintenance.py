"""Dry-run maintenance never deletes owner music or watchlists."""
from music_bot.anime import AnimeTitle
from music_bot.maintenance import prune_temporary_data
from music_bot.screen import ScreenTitle
from music_bot.storage import Library


def test_pruning_only_transient_records_and_dry_run(tmp_path):
    db=Library(tmp_path/"music.db")
    t=db.add_track("file","unique","Song","Artist")
    db.like(t.id)
    db.set_rating(t.id,9)
    db.create_playlist("Road")
    db.record_playback_event(t.id,"complete",dedupe_key="x")
    q=db.create_queue([t.id],"Old wave","balanced")
    db.mark_queue_sent(db.peek_queue(q))
    db.remember_anime([AnimeTitle(1,"Cowboy Bebop","Cowboy Bebop",None,
                                   26,"TV","FINISHED",1998,87,12)])
    db.update_anime_watch(1,watch_status="watching",progress=8,score=9)
    db.remember_screen_titles([ScreenTitle(2,"movie","Interstellar",
                                          "Interstellar",2014,None,8.7)])
    db.update_screen_watch("movie",2,watch_status="completed",score=9)
    with db.con() as connection:
        connection.execute(
            "UPDATE queues SET created_at=datetime('now','-100 days')")
        connection.execute(
            "UPDATE sent_history SET sent_at=datetime('now','-130 days')")
        connection.execute(
            "UPDATE playback_events SET created_at=datetime('now','-70 days')")
        connection.execute(
            "INSERT INTO widget_receipts(query_id,result,created_at) "
            "VALUES('query','ok',datetime('now','-40 days'))")
        connection.execute(
            "INSERT INTO external_catalog_cache(cache_key,payload,expires_at) "
            "VALUES('old','{}',1)")
    snapshot=prune_temporary_data(db.path)
    assert snapshot["old_queues"]==1
    assert snapshot["old_playback_events"]==1
    assert snapshot["old_sent_history"]==1
    assert snapshot["expired_catalog_cache"]==1
    with db.con() as connection:
        assert connection.execute("SELECT COUNT(*) FROM queues").fetchone()[0]==1
    result=prune_temporary_data(db.path,dry_run=False)
    assert result==snapshot
    with db.con() as connection:
        assert connection.execute("SELECT COUNT(*) FROM queues").fetchone()[0]==0
        assert connection.execute("SELECT COUNT(*) FROM sent_history").fetchone()[0]==0
        assert connection.execute("SELECT COUNT(*) FROM playback_events").fetchone()[0]==0
    assert db.get_track(t.id).file_id=="file"
    assert db.ratings()[t.id]==9
    assert db.liked_ids()=={t.id}
    assert db.playlists()
    assert db.anime_list()[0]["progress"]==8
    assert db.screen_watchlist()[0]["score"]==9
