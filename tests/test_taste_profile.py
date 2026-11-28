"""Taste profile reports actual current signals and never invents listening history."""
import asyncio

from fastapi.testclient import TestClient

from music_bot.api import create_app
from music_bot.artists import Artist
from music_bot.catalog import CatalogError
from music_bot.config import Settings
from music_bot.storage import Library
from music_bot.taste_profile import music_profile, explain_wave_track, profile_genres
from test_albums_api import auth, TOKEN, OWNER


def test_profile_uses_actual_wave_signals(tmp_path):
    db = Library(tmp_path / "music.db")
    a = db.add_track("f1","u1","Loved","Artist")
    b = db.add_track("f2","u2","Similar","Artist")
    c = db.add_track("f3","u3","Other","Different")
    d = db.add_track("f4","u4","Parked","Other artist")
    db.set_rating(a.id,9)
    db.like(a.id)
    db.set_track_verdict(d.id,"later")
    db.record_single_send(a.id)
    profile = music_profile(db)
    assert profile["library_count"] == 4
    assert profile["eligible_count"] == 3
    assert profile["categories"] == {"familiar":1,"similar":1,"new":1}
    assert profile["artists"][0]["name"] == "Artist"
    assert profile["recent_positive_artists"] == [{"name":"Artist","tracks":1}]
    assert not profile["historical_trend_available"]
    why = explain_wave_track(db,a.id)
    assert why["category"] == "familiar" and why["eligible"]
    assert "Твоя оценка: 9/10" in why["reasons"]
    assert any("Недавно отправлялся" in reason for reason in why["cautions"])
    assert explain_wave_track(db,b.id)["category"] == "similar"
    assert explain_wave_track(db,c.id)["category"] == "new"
    assert not explain_wave_track(db,d.id)["eligible"]
    assert db.playback_stats() == {}
    db.set_rating(a.id,2)
    assert not music_profile(db)["recent_positive_artists"]


def test_genres_are_explicit_saved_artist_metadata(tmp_path):
    db = Library(tmp_path / "music.db")
    ident = "00000000-0000-0000-0000-000000000001"
    db.remember_artists([Artist(ident,"Artist")])
    db.set_artist_saved(ident, True)

    class Catalog:
        async def _json(self, provider, path, params, **kwargs):
            assert provider == "mb" and path.endswith(ident)
            assert params["inc"] == "genres"
            return {"id":ident,"genres":[{"name":"jungle"},{"name":"jungle"},{"name":"electronic"}]}
    data = asyncio.run(profile_genres(db,Catalog()))
    assert data["artists"][0]["genres"] == ["jungle","electronic"]
    assert data["source"] == "MusicBrainz"

    class Offline:
        async def _json(self,*args,**kwargs):
            raise CatalogError("offline")
    assert asyncio.run(profile_genres(db,Offline()))["unavailable"] == 1


def test_taste_profile_owner_only_api(tmp_path):
    cfg = Settings(TOKEN,OWNER,tmp_path/"music.db")
    db = Library(cfg.db_path)
    track = db.add_track("f","u","Track","Artist")
    with TestClient(create_app(cfg)) as client:
        for path in ("/api/taste/profile","/api/taste/profile/genres",
                     f"/api/tracks/{track.id}/wave-explanation"):
            assert client.get(path).status_code == 401
            assert client.get(path,headers=auth(999)).status_code == 401
            assert client.get(path,headers=auth()).status_code == 200
        assert client.get("/api/tracks/999999/wave-explanation",headers=auth()).status_code == 404
        assert client.get("/api/taste/profile",headers=auth()).json()["library_count"] == 1
        assert "file_id" not in client.get(f"/api/tracks/{track.id}/wave-explanation",headers=auth()).text
