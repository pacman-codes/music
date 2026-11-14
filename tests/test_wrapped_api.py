"""Only Telegram owner can request private music profile."""
import hashlib,hmac,json,time
from urllib.parse import urlencode

from fastapi.testclient import TestClient
from music_bot.api import create_app
from music_bot.config import Settings
from music_bot.storage import Library

TOKEN="12345:token"
OWNER=42


def auth(owner=OWNER):
    d={"auth_date":str(int(time.time())),
       "user":json.dumps({"id":owner},separators=(",",":"))}
    key=hmac.new(b"WebAppData",TOKEN.encode(),hashlib.sha256).digest()
    check="\n".join(f"{k}={v}" for k,v in sorted(d.items()))
    d["hash"]=hmac.new(key,check.encode(),hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data":urlencode(d)}


def test_wrapped_endpoint_authorized_and_does_not_fake_listens(tmp_path):
    cfg=Settings(TOKEN,OWNER,tmp_path/"db.sqlite")
    db=Library(cfg.db_path)
    track=db.add_track("f","unique","Roads","Portishead")
    db.record_single_send(track.id)
    with TestClient(create_app(cfg)) as client:
        assert client.get("/api/stats/wrapped").status_code==401
        assert client.get("/api/stats/wrapped",headers=auth(999)).status_code==401
        assert client.get("/api/stats/wrapped?period=garbage",headers=auth()).status_code==422
        data=client.get("/api/stats/wrapped?period=30d",headers=auth())
        assert data.status_code==200
        payload=data.json()
        assert payload["deliveries"]==1
        assert payload["manual_completions"]==0
        assert "token" not in data.text
