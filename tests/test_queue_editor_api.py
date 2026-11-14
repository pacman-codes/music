"""Owner-only queue controller tests for Mini App."""
import hashlib,hmac,json,time
from urllib.parse import urlencode
from fastapi.testclient import TestClient
from music_bot.api import create_app
from music_bot.config import Settings
from music_bot.storage import Library

TOKEN="12345:fake"
OWNER=42

def auth(owner=OWNER):
    d={"auth_date":str(int(time.time())),
       "user":json.dumps({"id":owner},separators=(",",":"))}
    key=hmac.new(b"WebAppData",TOKEN.encode(),hashlib.sha256).digest()
    check="\n".join(f"{k}={v}" for k,v in sorted(d.items()))
    d["hash"]=hmac.new(key,check.encode(),hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data":urlencode(d)}


def test_queue_edit_endpoints_require_owner_and_change_only_pending(tmp_path):
    cfg=Settings(TOKEN,OWNER,tmp_path/"db.sqlite")
    db=Library(cfg.db_path)
    songs=[
        db.add_track(f"file{i}",f"u{i}",f"Track {i}","Band")
        for i in range(15)
    ]
    qid=db.create_queue([t.id for t in songs[:12]],"Mix","balanced")
    with TestClient(create_app(cfg)) as client:
        assert client.get("/api/queue/current").status_code==401
        assert client.get("/api/queue/current",headers=auth(11)).status_code==401
        state=client.get("/api/queue/current",headers=auth()).json()
        assert state["queue_id"]==qid
        assert state["filters"]["min_rating"]==0
        path=f"/api/queue/{qid}"
        denied=client.post(path+"/items",headers=auth(11),
                           json={"action":"next","track_id":songs[14].id})
        assert denied.status_code==401
        added=client.post(path+"/items",headers=auth(),json={
            "action":"next","track_id":songs[14].id,"expected_cursor":0
        })
        assert added.status_code==200
        assert added.json()["upcoming"][0]["track_id"]==songs[14].id
        changed=client.put(path+"/items",headers=auth(),json={
            "position":0,"to_position":2,"expected_cursor":0
        })
        assert changed.status_code==200
        assert changed.json()["upcoming"][2]["track_id"]==songs[14].id
        assert client.delete(path+"/items/2",headers=auth()).status_code==200
        payload={"min_rating":7,"liked_only":True,"no_repeat_today":True}
        filters=client.put(path+"/filters",headers=auth(),json=payload)
        assert filters.status_code==200
        assert filters.json()==payload
        assert client.get(path+"/filters",headers=auth()).json()==payload
        assert client.put(path+"/filters",headers=auth(),json={
            "min_rating":-1,"liked_only":True,"no_repeat_today":True
        }).status_code==422
