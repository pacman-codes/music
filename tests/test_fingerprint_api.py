"""Only the authenticated owner can ask AcoustID to identify Telegram audio."""
import hashlib,hmac,json,time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

from fastapi.testclient import TestClient
import music_bot.api as api_module

from music_bot.api import create_app
from music_bot.config import Settings
from music_bot.fingerprints import RecognitionResult,RecognitionCandidate
from music_bot.catalog import CatalogTrack
from music_bot.storage import Library

TOKEN="12345:fake"
OWNER=42
MBID="8a49dba0-253a-4535-b87f-78bb035336ce"


def auth(user=OWNER):
    d={"auth_date":str(int(time.time())),
       "user":json.dumps({"id":user},separators=(",",":"))}
    key=hmac.new(b"WebAppData",TOKEN.encode(),hashlib.sha256).digest()
    check="\n".join(f"{k}={v}" for k,v in sorted(d.items()))
    d["hash"]=hmac.new(key,check.encode(),hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data":urlencode(d)}


class FakeBot:
    def __init__(self,token):
        assert token==TOKEN
    async def __aenter__(self): return self
    async def __aexit__(self,*_): return None
    async def get_file(self, file_id):
        return SimpleNamespace(file_size=100,file_path="telegram-private-file")
    async def download_file(self,path,*,destination,timeout):
        Path(destination).write_bytes(b"ID3"+b"\0"*100)


class FakeRecognition:
    client_key="private-key"
    async def identify(self,path):
        assert path.exists()
        return RecognitionResult("b"*64,200,(
            RecognitionCandidate(CatalogTrack(MBID,"Roads","Portishead"),0.9),
        ))


def test_fingerprint_endpoint_disabled_or_owner_only(tmp_path,monkeypatch):
    cfg=Settings(TOKEN,OWNER,tmp_path/"db.sqlite")
    db=Library(cfg.db_path)
    t=db.add_track("file-id","file-unique","Roads","Portishead")
    monkeypatch.setattr(api_module,"Bot",FakeBot)
    with TestClient(create_app(cfg)) as client:
        assert client.get("/api/fingerprint/status").status_code==401
        assert client.get("/api/fingerprint/status",headers=auth()).json()["enabled"] is False
        assert client.post(f"/api/tracks/{t.id}/fingerprint",headers=auth()).status_code==409
    cfg=Settings(TOKEN,OWNER,tmp_path/"db.sqlite",None,(),False,False,None,"fake-key")
    with TestClient(create_app(cfg,recognizer=FakeRecognition())) as client:
        url=f"/api/tracks/{t.id}/fingerprint"
        assert client.post(url).status_code==401
        assert client.post(url,headers=auth(999)).status_code==401
        assert client.post("/api/tracks/999/fingerprint",headers=auth()).status_code==404
        data=client.post(url,headers=auth())
        assert data.status_code==200
        payload=data.json()
        assert payload["auto_matched"] is False
        assert payload["duplicates"]==[]
        assert payload["results"][0]["mbid"]==MBID
        assert "private-key" not in data.text
        assert db.recording_for_track(t.id) is None
