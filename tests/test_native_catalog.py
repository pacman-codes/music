"""Standalone catalog keeps metadata, approved sources and playable audio separate."""
from fastapi.testclient import TestClient

from music_bot.albums import Album, StoreTrack
from music_bot.artists import Artist, ReleaseGroup
from music_bot.catalog import CatalogTrack
from music_bot.native_api import create_app
from music_bot.native_prefetch import Prefetch
from music_bot.native_storage import NativeStorage
from music_bot.storage import Library


RECORDING="00000000-0000-0000-0000-000000000001"
ARTIST="00000000-0000-0000-0000-000000000002"
ALBUM="00000000-0000-0000-0000-000000000003"
GROUP="00000000-0000-0000-0000-000000000004"
HOST="audio.example.com"
URL="https://audio.example.com/song.mp3"


def owner_client(root,catalog=None):
    store=NativeStorage(Library(root/"music.db"),root/"audio")
    client=TestClient(create_app(root,catalog=catalog))
    code=store.issue_pairing()
    response=client.post("/v1/pair",json={"code":code,"label":"Phone"})
    assert response.status_code==200
    return client,{"Authorization":"Bearer "+response.json()["token"]},store


def test_catalog_search_decisions_and_source_preparation(tmp_path,monkeypatch):
    monkeypatch.setenv("LAIN_PREFETCH_HOSTS",HOST)
    row=CatalogTrack(RECORDING,"Song","Artist",ARTIST,120000)
    class Catalog:
        async def search(self,q,limit,allow_variants):
            assert q=="Artist Song" and limit==20 and not allow_variants
            return [row]
    client,headers,store=owner_client(tmp_path,Catalog())
    db=store.library
    assert client.get("/v1/discovery/search?q=Artist+Song").status_code==401
    found=client.get("/v1/discovery/search?q=Artist+Song",headers=headers).json()["results"][0]
    assert found["mbid"]==RECORDING and found["local_track_id"] is None
    assert not found["playable"] and not found["preparable"]
    assert client.put(f"/v1/discovery/{RECORDING}/saved",headers=headers,json={"saved":True}).status_code==200
    assert client.put(f"/v1/discovery/{RECORDING}/reaction",headers=headers,json={"status":"add"}).status_code==200
    assert db.all_tracks()==[]
    prefetch=Prefetch(store,(HOST,))
    tid=prefetch.approve_recording(RECORDING,URL)
    assert db.resolved_catalog_tracks()[RECORDING]==tid
    assert db.track_verdicts()[tid]=="add"
    assert prefetch.approve_recording(RECORDING,URL)==tid
    found=client.get("/v1/discovery/wishlist",headers=headers).json()["results"][0]
    assert found["local_track_id"]==tid and found["preparable"] and not found["playable"]
    assert URL not in str(found)
    assert client.post("/v1/prefetch",headers=headers,json={"track_ids":[tid]}).json()["results"][0]["state"]=="queued"
    assert not client.get("/v1/wave",headers=headers).json()["results"]
    source=tmp_path/"test.mp3"
    source.write_bytes(b"ID3"+b"x"*100)
    store.import_file(source,title="Song",artist="Artist",track_id=tid)
    assert client.get("/v1/discovery/wishlist",headers=headers).json()["results"][0]["playable"]
    assert client.get("/v1/wave",headers=headers).json()["results"][0]["id"]==tid
    other=db.add_track("f","u","Other","Band")
    assert client.put(f"/v1/discovery/{RECORDING}/match",headers=headers,json={"track_id":other.id}).status_code==409
    assert db.resolved_catalog_tracks()[RECORDING]==tid
    client.close()


def test_album_artist_and_playlist_native_collection(tmp_path,monkeypatch):
    track=CatalogTrack(RECORDING,"Song","Artist",ARTIST,100000)
    album=Album(ALBUM,"Album","Artist","2020",2,GROUP)
    async def find(catalog,q,exact):return [album]
    async def tracks(catalog,ident):
        if ident=="it-100":return "Apple Album","Artist",[StoreTrack("it-101","Song","Artist",100000)]
        return "Album","Artist",[track]
    async def artists(catalog,q):return [Artist(ARTIST,"Artist")]
    async def discography(catalog,ident,page):return [ReleaseGroup(GROUP,"Album","2020","Album")],False
    async def group(catalog,ident):return album
    for name,value in [("find_albums",find),("album_tracks",tracks),("search_artists",artists),
                       ("artist_discography",discography),("group_album",group)]:
        monkeypatch.setattr("music_bot.native_catalog."+name,value)
    client,headers,store=owner_client(tmp_path,object())
    db=store.library
    source=tmp_path/"one.mp3"
    source.write_bytes(b"ID3"+b"1"*100)
    tid=store.import_file(source,title="Song",artist="Artist")
    other=db.add_track("file","uid","Other","Band")
    assert client.get("/v1/albums/search?q=Artist",headers=headers).json()["results"][0]["mbid"]==ALBUM
    detail=client.get(f"/v1/albums/{ALBUM}",headers=headers).json()
    assert detail["available_count"]==0 and detail["tracks"][0]["local_track_id"] is None
    assert client.put(f"/v1/albums/{ALBUM}/saved",headers=headers,json={"saved":True}).status_code==200
    assert client.get("/v1/albums",headers=headers).json()["results"][0]["mbid"]==ALBUM
    assert client.put(f"/v1/discovery/{RECORDING}/match",headers=headers,json={"track_id":tid}).status_code==200
    assert client.get(f"/v1/albums/{ALBUM}",headers=headers).json()["available_count"]==1
    url="/v1/albums/it-100/tracks/0/match"
    assert client.put(url,headers=headers,json={"recording_id":"it-102","track_id":tid}).status_code==409
    assert client.put(url,headers=headers,json={"recording_id":"it-101","track_id":tid}).status_code==200
    assert client.get("/v1/albums/it-100",headers=headers).json()["available_count"]==1
    assert client.get("/v1/artists/search?q=Artist",headers=headers).json()["results"][0]["mbid"]==ARTIST
    assert client.put(f"/v1/artists/{ARTIST}/saved",headers=headers,json={"saved":True}).status_code==200
    assert client.get(f"/v1/artists/{ARTIST}",headers=headers).json()["results"][0]["id"]==tid
    assert client.get(f"/v1/artists/{ARTIST}/discography",headers=headers).json()["results"][0]["mbid"]==GROUP
    assert client.get(f"/v1/release-groups/{GROUP}",headers=headers).json()["release_id"]==ALBUM
    pid=client.post("/v1/playlists",headers=headers,json={"name":"Test"}).json()["id"]
    for ident in (tid,other.id):
        assert client.post(f"/v1/playlists/{pid}/tracks",headers=headers,json={"track_id":ident}).status_code==200
    assert client.put(f"/v1/playlists/{pid}/tracks/{other.id}/position",headers=headers,json={"delta":-1}).status_code==200
    rows=client.get(f"/v1/playlists/{pid}/queue",headers=headers).json()["results"]
    assert [(r["id"],r["playable"]) for r in rows]==[(other.id,False),(tid,True)]
    assert client.delete(f"/v1/playlists/{db.liked_id()}",headers=headers).status_code==409
    assert client.put(f"/v1/playlists/{pid}",headers=headers,json={"name":"Renamed"}).status_code==200
    assert client.delete(f"/v1/playlists/{pid}/tracks/{other.id}",headers=headers).status_code==200
    assert client.delete(f"/v1/playlists/{pid}",headers=headers).status_code==200
    assert db.get_track(tid)
    assert client.get("/v1/taste/profile",headers=headers).json()["available_count"]==1
    assert client.get("/v1/smart-playlists/unheard",headers=headers).json()["results"]
    payload={"track_id":tid,"play_id":"a"*32,"event":"start","listened_ms":3000,"duration_ms":100000}
    assert client.post("/v1/playback",headers=headers,json=payload).status_code==200
    assert tid not in {r["id"] for r in client.get("/v1/smart-playlists/unheard",headers=headers).json()["results"]}
    client.close()
