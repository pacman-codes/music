"""Owner-scoped Mini App rating/report API."""
import hashlib, hmac, json, time
from urllib.parse import urlencode
from fastapi.testclient import TestClient
from music_bot.api import create_app
from music_bot.config import Settings
from music_bot.storage import Library

TOKEN="1234:offline-token"
OWNER=123

def signed():
    fields={"auth_date":str(int(time.time())),
            "user":json.dumps({"id":OWNER},separators=(",",":"))}
    secret=hmac.new(b"WebAppData",TOKEN.encode(),hashlib.sha256).digest()
    check="\n".join(f"{k}={v}" for k,v in sorted(fields.items()))
    fields["hash"]=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    return urlencode(fields)

def test_auth_rating_and_report(tmp_path):
    cfg=Settings(TOKEN,OWNER,tmp_path/"music.db")
    db=Library(cfg.db_path)
    t=db.add_track("file","unique","Track","Artist")
    with TestClient(create_app(cfg)) as client:
        hdr={"X-Telegram-Init-Data":signed()}
        assert client.post(f"/api/rating/{t.id}",json={"score":8}).status_code==401
        for bad in (0,11,1.5,"8",True):
            assert client.post(f"/api/rating/{t.id}",headers=hdr,json={"score":bad}).status_code==422
        assert client.post(f"/api/report/{t.id}",headers=hdr,json={"event":"play"}).status_code==422
        assert client.post(f"/api/rating/{t.id}",headers=hdr,json={"score":9}).json()["rating"]==9
        assert client.post(f"/api/report/{t.id}",headers=hdr,json={"event":"skip"}).json()["source"]=="manual"
        assert client.post(f"/api/report/{t.id}",headers=hdr,json={"event":"complete"}).status_code==200
        data=client.get("/api/tracks",headers=hdr).json()[0]
        assert data["rating"]==9
        assert data["reported_skips"]==1 and data["reported_completions"]==1
        assert client.delete(f"/api/rating/{t.id}",headers=hdr).json()["changed"]
        assert client.get("/api/tracks",headers=hdr).json()[0]["rating"] is None
        assert client.post("/api/rating/99999",headers=hdr,json={"score":8}).status_code==404
