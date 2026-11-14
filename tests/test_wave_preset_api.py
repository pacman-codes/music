"""The Wave 2.0 mix control is owner-only and persisted."""
import hashlib,hmac,json,time
from urllib.parse import urlencode
from fastapi.testclient import TestClient

from music_bot.api import create_app
from music_bot.config import Settings
from music_bot.storage import Library

TOKEN="12345:test-token"
OWNER=42


def auth(user=OWNER):
    d={"auth_date":str(int(time.time())),
       "user":json.dumps({"id":user},separators=(",",":"))}
    key=hmac.new(b"WebAppData",TOKEN.encode(),hashlib.sha256).digest()
    check="\n".join(f"{x}={y}" for x,y in sorted(d.items()))
    d["hash"]=hmac.new(key,check.encode(),hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data":urlencode(d)}


def test_wave_preset_authorized_and_persists(tmp_path):
    cfg=Settings(TOKEN,OWNER,tmp_path/"db.sqlite")
    with TestClient(create_app(cfg)) as client:
        assert client.get("/api/wave/preset").status_code==401
        assert client.put("/api/wave/preset",headers=auth(99),
                          json={"preset":"explore"}).status_code==401
        assert client.get("/api/wave/preset",headers=auth()).json()=={
            "preset":"balanced"
        }
        assert client.put("/api/wave/preset",headers=auth(),
                          json={"preset":"nonsense"}).status_code==422
        changed=client.put("/api/wave/preset",headers=auth(),
                           json={"preset":"explore"})
        assert changed.status_code==200
        assert changed.json()=={"preset":"explore"}
        assert Library(cfg.db_path).get_wave_preset()=="explore"
