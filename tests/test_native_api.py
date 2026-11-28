from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from music_bot.native_api import create_app
from music_bot.native_cli import clone_library
from music_bot.native_storage import NativeStorage, digest
from music_bot.storage import Library


def test_single_use_pairing_expiry_revocation(tmp_path):
    db=Library(tmp_path/"music.db")
    store=NativeStorage(db,tmp_path/"audio")
    code=store.issue_pairing(now=100)
    with pytest.raises(ValueError):
        store.exchange(code,"Phone",now=700)
    code=store.issue_pairing(now=1000)
    session=store.exchange(code,"Phone",now=1001)
    with pytest.raises(ValueError):
        store.exchange(code,"Other",now=1002)
    assert store.authenticate(session["token"],now=1002)==session["device_id"]
    assert store.authenticate(session["token"],now=session["expires_at"]) is None
    with db.con() as con:
        assert con.execute("SELECT digest FROM native_sessions").fetchone()[0]==digest(session["token"])
    assert "digest" not in store.devices()[0]
    assert store.revoke(session["device_id"])
    assert store.authenticate(session["token"],now=1003) is None


def test_native_import_and_protected_range_stream(tmp_path):
    root=tmp_path/"native"
    db=Library(root/"music.db")
    store=NativeStorage(db,root/"audio")
    # Synthetic bytes test transport, not codec validity.
    source=tmp_path/"sample.mp3"
    source.write_bytes(b"ID3"+b"x"*100)
    legacy=db.add_track("telegram-file","legacy","Old","Artist")
    db.set_rating(legacy.id,9)
    tid=store.import_file(source,title="Native",artist="Band")
    assert tid==store.import_file(source,title="Duplicate",artist="Band")
    assert store.available_ids()=={tid}
    with TestClient(create_app(root)) as client:
        for path in ("/v1/tracks","/v1/wave",f"/v1/tracks/{tid}/audio"):
            assert client.get(path).status_code==401
        code=store.issue_pairing()
        pair=client.post("/v1/pair",json={"code":code,"label":"Phone"})
        assert pair.status_code==200
        hdr={"Authorization":"Bearer "+pair.json()["token"]}
        assert client.post("/v1/pair",json={"code":code,"label":"Again"}).status_code==401
        rows=client.get("/v1/tracks",headers=hdr).json()["results"]
        assert {r["id"]:r["playable"] for r in rows}=={legacy.id:False,tid:True}
        assert "telegram-file" not in str(rows)
        path=f"/v1/tracks/{tid}/audio"
        response=client.get(path,headers={**hdr,"Range":"bytes=0-2"})
        assert response.status_code==206
        assert response.content==b"ID3"
        assert response.headers["content-range"]=="bytes 0-2/103"
        assert client.get(f"/v1/tracks/{legacy.id}/audio",headers=hdr).status_code==404
        assert [r["id"] for r in client.get("/v1/wave",headers=hdr).json()["results"]]==[tid]
        assert client.put(f"/v1/tracks/{tid}/rating",headers=hdr,json={"score":9}).status_code==200
        assert client.put(f"/v1/tracks/{tid}/reaction",headers=hdr,json={"status":"add"}).status_code==200
        assert db.ratings()[tid]==9
        assert client.delete("/v1/session",headers=hdr).status_code==200
        assert client.get(path,headers=hdr).status_code==401
    assert db.playback_stats()=={}
    store.import_file(source,title="Old",artist="Artist",track_id=legacy.id)
    assert db.ratings()[legacy.id]==9
    assert store.available_ids()=={legacy.id,tid}
    store.audio(tid)[0].unlink()
    assert store.available_ids()==set()


def test_clone_preserves_existing_ids_and_refuses_overwrite(tmp_path):
    source=Library(tmp_path/"old.db")
    track=source.add_track("f","u","Track","Artist")
    source.set_rating(track.id,10)
    path=tmp_path/"new"/"music.db"
    clone_library(source.path,path)
    copy=Library(path)
    assert copy.get_track(track.id)==track
    assert copy.ratings()[track.id]==10
    copy.set_rating(track.id,2)
    assert source.ratings()[track.id]==10
    with pytest.raises(FileExistsError):
        clone_library(source.path,path)
