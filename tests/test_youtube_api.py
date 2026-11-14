"""YouTube feature flags and owner protection in Mini App API."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from music_bot.api import create_app
from music_bot.config import Settings
from music_bot.youtube import VideoCandidate

TOKEN="123:fake"
OWNER=42
ID="dQw4w9WgXcQ"


def auth(user=OWNER):
    fields={"auth_date":str(int(time.time())),
            "user":json.dumps({"id":user},separators=(",",":"))}
    key=hmac.new(b"WebAppData",TOKEN.encode(),hashlib.sha256).digest()
    msg="\n".join(f"{k}={v}" for k,v in sorted(fields.items()))
    fields["hash"]=hmac.new(key,msg.encode(),hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data":urlencode(fields)}


class StubYoutube:
    def __init__(self):
        self.queries=[]
    async def search(self,query,*,limit):
        self.queries.append((query,limit))
        return [VideoCandidate(ID,"Example","Creator",180)]


class StubIngest:
    def __init__(self):
        self.calls=[]
    async def import_video(self,video_id):
        self.calls.append(video_id)
        return {"track_id":12,"reused":False}


def test_youtube_disabled_by_default(tmp_path):
    cfg=Settings(TOKEN,OWNER,tmp_path/"db.sqlite")
    with TestClient(create_app(cfg,youtube_source=StubYoutube())) as client:
        assert client.get("/api/youtube/status").status_code==401
        assert client.get("/api/youtube/status",headers=auth()).json()["search_enabled"] is False
        assert client.get("/api/youtube/search?q=Example",headers=auth()).status_code==409
        assert client.post(f"/api/youtube/import/{ID}",headers=auth()).status_code==409


def test_youtube_search_and_owner_only_import(tmp_path):
    cfg=Settings(TOKEN,OWNER,tmp_path/"db.sqlite",None,(),True,True)
    youtube=StubYoutube()
    ingest=StubIngest()
    with TestClient(create_app(cfg,youtube_source=youtube,
                               youtube_ingest=ingest)) as client:
        assert client.get("/api/youtube/search?q=Example").status_code==401
        assert client.get("/api/youtube/search?q=Example",headers=auth(100)).status_code==401
        result=client.get("/api/youtube/search?q=Example",headers=auth())
        assert result.status_code==200
        assert result.json()["results"][0]["playable"] is False
        assert youtube.queries==[("Example",6)]
        assert client.post(f"/api/youtube/import/{ID}",headers=auth(100)).status_code==401
        assert client.post("/api/youtube/import/invalid",headers=auth()).status_code==422
        imported=client.post(f"/api/youtube/import/{ID}",headers=auth())
        assert imported.status_code==200 and imported.json()["track_id"]==12
        assert ingest.calls==[ID]
