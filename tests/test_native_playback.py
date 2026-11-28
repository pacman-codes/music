from fastapi.testclient import TestClient

from music_bot.native_api import create_app
from music_bot.native_playback import NativePlayback
from music_bot.native_storage import NativeStorage
from music_bot.storage import Library


def test_native_play_reports_are_idempotent_and_conservative(tmp_path):
    db=Library(tmp_path/"music.db")
    a=db.add_track("f","u","Track","Artist")
    b=db.add_track("g","v","Other","Band")
    p=NativePlayback(db)
    ident="a"*32
    assert not p.report("device",ident,a.id,"start",1999,100000)
    assert p.report("device",ident,a.id,"start",2000,100000)
    assert not p.report("device",ident,a.id,"start",2000,100000)
    assert p.report("device",ident,a.id,"complete",90000,100000)
    assert not p.report("device",ident,a.id,"skip",90000,100000)
    assert db.playback_stats()[a.id]==(0,1)
    assert p.report("device","b"*32,b.id,"complete",2000,100000)
    assert b.id not in db.playback_stats()
    assert p.report("device","c"*32,b.id,"skip",5000,100000)
    assert db.playback_stats()[b.id]==(1,0)
    assert p.report("device","d"*32,b.id,"stop",5000,100000)
    assert db.playback_stats()[b.id]==(1,0)
    assert p.context(10)["rotation_ids"]=={a.id,b.id}
    assert p.history()["results"][0]["outcome"]=="stop"
    assert db.recent_ids()==set()


def test_native_playback_api_and_rotation(tmp_path):
    db=Library(tmp_path/"music.db")
    store=NativeStorage(db,tmp_path/"audio")
    tracks=[]
    for i in range(12):
        path=tmp_path/f"{i}.mp3"
        path.write_bytes(b"ID3"+bytes([i])*100)
        tid=store.import_file(path,title=f"Song {i}",artist=f"Artist {i}")
        tracks.append(tid)
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/v1/history").status_code==401
        code=store.issue_pairing()
        token=client.post("/v1/pair",json={"code":code,"label":"Phone"}).json()["token"]
        headers={"Authorization":"Bearer "+token}
        payload={"track_id":tracks[0],"play_id":"f"*32,"event":"start","listened_ms":3000,"duration_ms":100000}
        assert client.post("/v1/playback",json=payload).status_code==401
        assert client.post("/v1/playback",json=payload,headers=headers).json()["changed"]
        assert not client.post("/v1/playback",json=payload,headers=headers).json()["changed"]
        assert client.post("/v1/playback",json={**payload,"track_id":tracks[1]},headers=headers).status_code==422
        wave=client.get("/v1/wave",headers=headers).json()["results"]
        assert tracks[0] not in {r["id"] for r in wave}
        assert len(wave)==10
        assert client.get("/v1/history",headers=headers).json()["results"][0]["track_id"]==tracks[0]
        settings={"diversity":75,"repeat_window":30,"preset":"explore"}
        assert client.put("/v1/wave/settings",json=settings,headers=headers).json()==settings
        assert client.get("/v1/wave/settings",headers=headers).json()==settings
