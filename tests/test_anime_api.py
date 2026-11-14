"""Owner-only AniList watchlist, no media playback surfaced."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from fastapi.testclient import TestClient
from music_bot.api import create_app
from music_bot.anime import AnimeTitle
from music_bot.config import Settings

TOKEN="1234:test"
OWNER=42
ROW=AnimeTitle(1,"Cowboy Bebop","Cowboy Bebop",None,26,"TV","FINISHED",1998,87,12)


def auth(user=OWNER):
    info={"auth_date":str(int(time.time())),
          "user":json.dumps({"id":user},separators=(",",":"))}
    key=hmac.new(b"WebAppData",TOKEN.encode(),hashlib.sha256).digest()
    check="\n".join(f"{x}={y}" for x,y in sorted(info.items()))
    info["hash"]=hmac.new(key,check.encode(),hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data":urlencode(info)}


class StubAniList:
    def __init__(self):
        self.calls=[]
    async def search(self,term,limit=12):
        self.calls.append((term,limit))
        return [ROW]


def test_anime_api_auth_and_progress(tmp_path):
    cfg=Settings(TOKEN,OWNER,tmp_path/"db.sqlite")
    catalog=StubAniList()
    with TestClient(create_app(cfg,anime_catalog=catalog)) as client:
        assert client.get("/api/anime/search?q=Cowboy").status_code==401
        assert client.get("/api/anime/search?q=Cowboy",headers=auth(999)).status_code==401
        result=client.get("/api/anime/search?q=Cowboy",headers=auth())
        assert result.status_code==200
        assert result.json()["results"][0]["playable"] is False
        assert "file_id" not in result.text
        assert catalog.calls==[("Cowboy",12)]
        update=client.post("/api/anime/watchlist/1",headers=auth(),
              json={"watch_status":"watching","progress":3,"score":8})
        assert update.status_code==200
        assert update.json()["progress"]==3
        listing=client.get("/api/anime/watchlist",headers=auth()).json()
        assert listing["results"][0]["title"]=="Cowboy Bebop"
        assert listing["results"][0]["playable"] is False
        assert client.post("/api/anime/watchlist/1",headers=auth(),
              json={"watch_status":"completed","progress":27,"score":9}).status_code==422
        assert client.post("/api/anime/watchlist/100",headers=auth(),
              json={"watch_status":"planned"}).status_code==422
        assert client.delete("/api/anime/watchlist/1",headers=auth()).json()["removed"]


def test_anime_input_validation(tmp_path):
    cfg=Settings(TOKEN,OWNER,tmp_path/"db.sqlite")
    with TestClient(create_app(cfg,anime_catalog=StubAniList())) as client:
        hdr=auth()
        assert client.get("/api/anime/search?q=x",headers=hdr).status_code==422
        assert client.post("/api/anime/watchlist/1",headers=hdr,
             json={"watch_status":"adult","progress":0}).status_code==422
