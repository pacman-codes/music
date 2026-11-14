"""Music Wrapped reports deliveries, NOT imaginary Telegram playback."""
from datetime import datetime, timezone

import pytest

from music_bot.storage import Library
from music_bot.wrapped import wrapped_summary


def test_wrapped_reports_real_deliveries_likes_and_manual_signals(tmp_path):
    db=Library(tmp_path/"music.db")
    a=db.add_track("tg-a","uid-a","Roads","Portishead")
    b=db.add_track("tg-b","uid-b","Teardrop","Massive Attack")
    db.like(a.id)
    db.set_rating(a.id,10)
    db.set_rating(b.id,8)
    db.record_single_send(a.id)
    db.record_single_send(a.id)
    db.record_single_send(b.id)
    db.record_playback_event(b.id,"skip",source="manual")
    report=wrapped_summary(db,"30d")
    assert report["deliveries"]==3
    assert report["distinct_sent_tracks"]==2
    assert report["manual_skips"]==1
    assert report["manual_completions"]==0
    assert report["favorites_count"]==1
    assert report["rated_count"]==2
    assert report["average_rating"]==9
    assert report["top_artists"][0]["artist"]=="Portishead"
    assert report["top_artists"][0]["deliveries"]==2
    assert report["favorite_artists"][0]["artist"]=="Portishead"
    assert report["statistics_basis"]=="telegram_deliveries_and_explicit_manual_feedback"
    assert "listening_minutes" not in report
    assert "play_count" not in report
    assert sum(d["deliveries"] for d in report["daily_deliveries"])==3


def test_wrapped_obeys_period_and_handles_empty_collection(tmp_path):
    db=Library(tmp_path/"music.db")
    empty=wrapped_summary(db,"7d")
    assert empty["deliveries"]==0
    assert empty["top_artists"]==[]
    assert empty["average_rating"] is None
    assert len(empty["daily_deliveries"])==7
    a=db.add_track("tg","uid","Song","Band")
    db.record_single_send(a.id)
    with db.con() as con:
        con.execute(
            "UPDATE sent_history SET sent_at=datetime('now','-100 days')"
        )
    assert wrapped_summary(db,"7d")["deliveries"]==0
    assert wrapped_summary(db,"365d")["deliveries"]==1
    assert wrapped_summary(db,"all")["deliveries"]==1
    with pytest.raises(ValueError):
        wrapped_summary(db,"not-period")
