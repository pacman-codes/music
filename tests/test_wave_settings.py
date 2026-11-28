"""Shared Wave settings preserve default behavior and strict queue constraints."""
import pytest
from fastapi.testclient import TestClient

from music_bot.api import create_app
from music_bot.config import Settings
from music_bot.recommend import rotation_pool, track_weight
from music_bot.storage import Library
from music_bot.telegram_wave_settings import wave_settings_page
from music_bot.selection import select_wave_batch
from test_albums_api import auth, TOKEN, OWNER


def test_settings_persist_and_validate(tmp_path):
    path = tmp_path / "music.db"
    db = Library(path)
    assert db.get_wave_settings() == {"diversity":50,"repeat_window":10}
    db.set_wave_settings(diversity=85)
    assert Library(path).get_wave_settings() == {"diversity":85,"repeat_window":10}
    db.set_wave_settings(repeat_window=0)
    assert db.get_wave_settings() == {"diversity":85,"repeat_window":0}
    for invalid in (-1,101,True,1.2,"50"):
        with pytest.raises(ValueError):
            db.set_wave_settings(diversity=invalid)
    text, markup = wave_settings_page(db)
    assert "85%" in text
    assert any(b.callback_data=="waveset:reset" for row in markup.inline_keyboard for b in row)


def test_diversity_controls_and_repeat_context(tmp_path):
    db = Library(tmp_path / "music.db")
    tracks = [db.add_track(f"f{i}", f"u{i}", f"Song {i}", f"Band {i%3}") for i in range(24)]
    for t in tracks[:15]:
        db.record_single_send(t.id)
    assert len(db.wave_recent_context(0)["rotation_ids"]) == 0
    assert len(db.wave_recent_context(0)["immediate_ids"]) == 3
    assert len(db.wave_recent_context(10)["rotation_ids"]) == 10
    assert len(db.wave_recent_context(30)["rotation_ids"]) == 15
    pool=tracks[:3]
    assert rotation_pool(pool,set(),set(),["band 0"],0)==pool
    assert rotation_pool(pool,set(),set(),["band 0"],50)==pool[1:]
    assert rotation_pool(pool,set(),set(),["band 1","band 0"],100)==pool[2:]
    kwargs=dict(liked_ids=set(),favorite_artists=set(),seen_artist={"band 0":2},recent_artists=["band 0"])
    assert track_weight(pool[0],**kwargs,diversity=100)<track_weight(pool[0],**kwargs,diversity=0)
    db.set_wave_settings(diversity=100,repeat_window=30)
    picked=select_wave_batch(db,"balanced",count=8)
    assert {t.id for t in picked}.isdisjoint(t.id for t in tracks[:15])
    assert db.playback_stats()=={}


def test_wave_settings_api(tmp_path):
    cfg=Settings(TOKEN,OWNER,tmp_path/"music.db")
    db=Library(cfg.db_path)
    with TestClient(create_app(cfg)) as client:
        path="/api/wave/settings"
        assert client.get(path).status_code==401
        assert client.put(path,json={"diversity":100}).status_code==401
        assert client.put(path,headers=auth(999),json={"diversity":100}).status_code==401
        assert client.get(path,headers=auth()).json()=={"diversity":50,"repeat_window":10}
        assert client.put(path,headers=auth(),json={"diversity":75,"repeat_window":60}).json()=={"diversity":75,"repeat_window":60}
        assert client.put(path,headers=auth(),json={"diversity":0}).json()=={"diversity":0,"repeat_window":60}
        for payload in ({"diversity":101},{"repeat_window":-1},{"diversity":True},{"diversity":"75"}):
            assert client.put(path,headers=auth(),json=payload).status_code==422
        assert db.get_wave_settings()=={"diversity":0,"repeat_window":60}
        assert db.get_wave_preset()=="balanced"
