"""Native prefetch only fetches explicitly approved audio; no external network in tests."""
import asyncio
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from music_bot.importer import MediaImportError
from music_bot.native_api import create_app
from music_bot.native_prefetch import Prefetch, MAX_ATTEMPTS
from music_bot.native_storage import NativeStorage
from music_bot.storage import Library


HOSTS=("audio.example.com",)
URL="https://audio.example.com/song.mp3"


def setup(tmp_path):
    db=Library(tmp_path/"music.db")
    store=NativeStorage(db,tmp_path/"audio")
    return db,store,Prefetch(store,HOSTS)


def track(db,n):
    return db.add_track(f"f{n}",f"u{n}",f"Song {n}",f"Artist {n}")


def test_jobs_deduplicated_and_recovered(tmp_path):
    db,store,p=setup(tmp_path)
    a,b=track(db,1),track(db,2)
    p.approve(a.id,URL)
    with pytest.raises(MediaImportError):
        p.approve(b.id,"http://127.0.0.1/song.mp3")
    with pytest.raises(ValueError):
        p.approve(b.id,URL+"?token=secret")
    assert p.enqueue([a.id,a.id,b.id],now=100)==[
        {"track_id":a.id,"state":"queued"},{"track_id":b.id,"state":"unavailable"}]
    job=p.claim(now=100)
    assert job["attempts"]==1
    assert p.claim(now=101) is None
    assert p.enqueue([a.id],now=101)[0]["state"]=="running"
    other=Prefetch(store,HOSTS)
    recovered=other.claim(now=281)
    assert recovered["attempts"]==2
    assert not p.finish(job,True,now=282)
    assert other.finish(recovered,False,"source_or_storage_error",now=282)
    assert other.claim(now=283) is None
    final=other.claim(now=342)
    assert final["attempts"]==MAX_ATTEMPTS
    assert other.finish(final,False,"source_or_storage_error",now=343)
    assert other.enqueue([a.id],now=1000)[0]["state"]=="failed"
    p.approve(a.id,URL)
    assert p.enqueue([a.id],now=1001)[0]["state"]=="queued"


def test_prefetch_worker_publishes_to_existing_track(tmp_path,monkeypatch):
    db,store,p=setup(tmp_path)
    a=track(db,1)
    db.set_rating(a.id,9)
    p.approve(a.id,URL)
    p.enqueue([a.id])
    source=tmp_path/"test.mp3"
    source.write_bytes(b"ID3"+b"x"*100)

    @asynccontextmanager
    async def download(url,hosts):
        assert url==URL and hosts==HOSTS
        yield source

    from collections import namedtuple
    Usage=namedtuple("Usage","total used free")
    monkeypatch.setattr("music_bot.native_prefetch.shutil.disk_usage",
                        lambda path:Usage(10**10,0,10**10))
    assert asyncio.run(p.run_one(download))
    assert p.status([a.id])[0]["state"]=="ready"
    assert store.audio(a.id)[0].read_bytes()==source.read_bytes()
    assert db.ratings()[a.id]==9
    assert db.playback_stats()=={}
    assert not asyncio.run(p.run_one(download))


def test_wave_prepares_only_approved_missing_music(tmp_path,monkeypatch):
    monkeypatch.setenv("LAIN_PREFETCH_HOSTS",HOSTS[0])
    db,store,p=setup(tmp_path)
    ready,missing,unapproved=[track(db,i) for i in range(3)]
    source=tmp_path/"test.mp3"
    source.write_bytes(b"ID3"+b"x"*100)
    store.import_file(source,title=ready.title,artist=ready.artist,track_id=ready.id)
    p.approve(missing.id,URL)
    with TestClient(create_app(tmp_path)) as client:
        for path in ("/v1/wave/prepare","/v1/prefetch","/v1/prefetch/status"):
            assert client.post(path,json={"track_ids":[missing.id]}).status_code==401
        code=store.issue_pairing()
        token=client.post("/v1/pair",json={"code":code,"label":"Phone"}).json()["token"]
        headers={"Authorization":"Bearer "+token}
        response=client.post("/v1/wave/prepare",headers=headers)
        assert response.status_code==200
        data=response.json()
        assert [x["id"] for x in data["results"]]==[ready.id]
        assert [x["id"] for x in data["preparing"]]==[missing.id]
        assert data["preparing"][0]["state"]=="queued"
        assert not data["preparing"][0]["playable"]
        assert URL not in response.text
        result=client.post("/v1/prefetch",headers=headers,json={
            "track_ids":[ready.id,missing.id,unapproved.id]}).json()["results"]
        assert [r["state"] for r in result]==["ready","queued","unavailable"]
        assert client.post("/v1/prefetch",headers=headers,json={"track_ids":[True]}).status_code==422
        assert client.post("/v1/prefetch",headers=headers,json={"track_ids":[1,2,3,4,5,6]}).status_code==422
