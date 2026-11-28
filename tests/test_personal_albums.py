"""Personal album metadata recommendations do not imply audio availability."""
import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient

from music_bot.api import create_app
from music_bot.artists import Artist
from music_bot.catalog import CatalogError, CatalogTrack
from music_bot.config import Settings
from music_bot.personal_albums import personal_albums, recommend_albums
from music_bot.storage import Library
from test_albums_api import auth, OWNER, TOKEN


def ident(i):
    return str(uuid.UUID(int=i))


class Catalog:
    offline = False

    async def artist_radio_by_id(self, mbid, **kwargs):
        return [CatalogTrack(ident(91), "Related song", "Related", ident(3))]

    async def _json(self, provider, path, params, **kwargs):
        if self.offline:
            raise CatalogError("Unavailable")
        assert provider == "mb"
        if path == "/ws/2/artist/":
            return {"artists": [{"id": ident(2), "name": "Rated Artist"}]}
        if path == "/ws/2/artist/"+ident(3):
            return {"id":ident(3),"name":"Related"}
        if path == "/ws/2/release-group/":
            artist = next(i for i in [1,2,3] if ident(i) in params["query"])
            name = {1:"Favorite",2:"Rated Artist",3:"Related"}[artist]
            credit = [{"artist":{"id":ident(artist),"name":name}}]
            return {"count":11,"release-groups":[
                {"id":ident(artist*10+i),"title":title,"primary-type":"Album",
                 "artist-credit":credit,"first-release-date":"2020"}
                for i,title in [(1,"Saved"),(2,"New")]]}
        raise AssertionError(path)


def test_personal_albums_mix_explicit_favorites_and_ratings(tmp_path):
    db = Library(tmp_path/"db.sqlite")
    db.remember_artists([Artist(ident(1),"Favorite")])
    db.set_artist_saved(ident(1), True)
    track=db.add_track("f","u","Song","Rated Artist")
    db.set_rating(track.id, 9)
    db.save_album(ident(100), "Saved", "Favorite", 5)
    result=asyncio.run(personal_albums(db,Catalog()))
    assert result["seed_artists"]==["Favorite","Rated Artist"]
    assert {(r["artist"],r["reason"]) for r in result["results"]}=={
        ("Favorite","preferred"),("Rated Artist","preferred"),("Related","related")}
    assert not any(r["artist"]=="Favorite" and r["title"]=="Saved" for r in result["results"])
    assert all(not r["playable"] and r["cover_urls"][0].endswith("/front-250") for r in result["results"])
    assert result["has_more"]
    assert len(db.all_tracks())==1 and len(db.saved_albums())==1
    assert db.playback_stats()=={}
    lease=db.acquire_media_import("taste","personal-albums",ttl=180)
    with pytest.raises(CatalogError):
        asyncio.run(recommend_albums(db,Catalog()))
    db.release_media_import("taste","personal-albums",lease)
    offline=Catalog()
    offline.offline=True
    with pytest.raises(CatalogError):
        asyncio.run(recommend_albums(db,offline))


def test_album_recommendations_api_auth_and_cold_start(tmp_path):
    cfg=Settings(TOKEN,OWNER,tmp_path/"db.sqlite")
    catalog=Catalog()
    with TestClient(create_app(cfg,catalog=catalog)) as client:
        path="/api/albums/for-you"
        assert client.get(path).status_code==401
        assert client.get(path,headers=auth(999)).status_code==401
        assert client.get(path+"?page=5",headers=auth()).status_code==422
        assert client.get(path,headers=auth()).json()["results"]==[]
        db=Library(cfg.db_path)
        db.remember_artists([Artist(ident(1),"Favorite")])
        db.set_artist_saved(ident(1),True)
        response=client.get(path,headers=auth())
        assert response.status_code==200
        assert response.json()["results"]
        assert "file_id" not in response.text
