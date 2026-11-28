from fastapi.testclient import TestClient

from music_bot.native_api import create_app
from music_bot.native_storage import NativeStorage
from music_bot.storage import Library


def test_device_checkpoints_are_independent_and_preserve_order(tmp_path):
    db=Library(tmp_path/"music.db")
    store=NativeStorage(db,tmp_path/"audio")
    tracks=[]
    for i in range(3):
        file=tmp_path/f"{i}.mp3"
        file.write_bytes(b"ID3"+bytes([i])*100)
        tracks.append(store.import_file(file,title=f"Song {i}",artist="Band"))
    a,b,c=tracks
    missing=db.add_track("file","uid","Not migrated","Band").id
    with TestClient(create_app(tmp_path)) as client:
        def pair(label):
            data=client.post("/v1/pair",json={"code":store.issue_pairing(),"label":label}).json()
            return data["device_id"],{"Authorization":"Bearer "+data["token"]}
        phone,h1=pair("Android")
        iphone,h2=pair("iPhone")
        payload={"track_ids":[a,missing,b,a,c],"index":2,"position_ms":42000,"wave":"balanced"}
        assert client.get("/v1/devices").status_code==401
        assert client.put("/v1/checkpoint",json=payload).status_code==401
        assert client.get(f"/v1/devices/{phone}/checkpoint",headers=h2).status_code==404
        assert client.put("/v1/checkpoint",json=payload,headers=h1).json()["revision"]==1
        assert client.put("/v1/checkpoint",json={**payload,"position_ms":43000},headers=h1).json()["revision"]==2
        assert client.put("/v1/checkpoint",json={"track_ids":[c,b],"index":0,"position_ms":1000},headers=h2).status_code==200
        listing=client.get("/v1/devices",headers=h2).json()["results"]
        assert {x["id"] for x in listing}=={phone,iphone}
        assert next(x for x in listing if x["id"]==iphone)["current"]
        assert next(x for x in listing if x["id"]==phone)["checkpoint"]["position_ms"]==43000
        assert "digest" not in str(listing) and "token" not in str(listing)
        data=client.get(f"/v1/devices/{phone}/checkpoint",headers=h2).json()
        assert [x["id"] for x in data["results"]]==[a,b,a,c]
        assert data["index"]==1 and data["omitted"]==1 and data["position_ms"]==43000
        assert data["revision"]==2 and data["wave"]=="balanced"
        assert client.get(f"/v1/devices/{iphone}/checkpoint",headers=h1).json()["position_ms"]==1000
        for bad in [{**payload,"index":5},{**payload,"track_ids":[True],"index":0},
                    {**payload,"track_ids":[999999],"index":0},{**payload,"position_ms":-1},
                    {**payload,"track_ids":[a]*1001}]:
            assert client.put("/v1/checkpoint",json=bad,headers=h1).status_code==422
        assert client.get(f"/v1/devices/{phone}/checkpoint",headers=h2).json()["revision"]==2
        assert client.put("/v1/checkpoint",json={**payload,"index":1},headers=h1).status_code==200
        assert client.get(f"/v1/devices/{phone}/checkpoint",headers=h2).status_code==409
        assert client.delete("/v1/checkpoint",headers=h1).json()["changed"]
        assert client.get(f"/v1/devices/{phone}/checkpoint",headers=h2).status_code==404
        assert client.get(f"/v1/devices/{iphone}/checkpoint",headers=h1).status_code==200
        assert client.put("/v1/checkpoint",json=payload,headers=h1).status_code==200
        assert client.delete("/v1/session",headers=h1).status_code==200
        assert client.get("/v1/devices",headers=h1).status_code==401
        assert client.get(f"/v1/devices/{phone}/checkpoint",headers=h2).status_code==404
        assert len(client.get("/v1/devices",headers=h2).json()["results"])==1
        with db.con() as con:
            assert con.execute("SELECT COUNT(*) FROM native_checkpoints WHERE device_id=?",(phone,)).fetchone()[0]==0
        assert db.playback_stats()=={}
        assert client.get("/v1/history",headers=h2).json()["results"]==[]
