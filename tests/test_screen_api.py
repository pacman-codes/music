"""TMDB is opt-in; only owner can query or modify lists."""
import hashlib,hmac,json,time
from urllib.parse import urlencode

from fastapi.testclient import TestClient
from music_bot.api import create_app
from music_bot.config import Settings
from music_bot.screen import ScreenTitle

TOKEN="1234:local-test"
OWNER=42


def auth(user=OWNER):
    d={"auth_date":str(int(time.time())),
       "user":json.dumps({"id":user},separators=(",",":"))}
    key=hmac.new(b"WebAppData",TOKEN.encode(),hashlib.sha256).digest()
    check="\n".join(f"{x}={y}" for x,y in sorted(d.items()))
    d["hash"]=hmac.new(key,check.encode(),hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data":urlencode(d)}


class FakeTmdb:
    async def search(self,query,limit):
        assert query=="Interstellar"
        return [ScreenTitle(157336,"movie","Interstellar","Interstellar",
                            2014,"Space",8.7)]


def test_tmdb_is_disabled_without_key(tmp_path):
    cfg=Settings(TOKEN,OWNER,tmp_path/"db.sqlite")
    with TestClient(create_app(cfg)) as client:
        assert client.get("/api/screen/status",headers=auth()).json()["enabled"] is False
        assert client.get("/api/screen/search?q=Interstellar",headers=auth()).status_code==409


def test_owner_only_tmdb_search_and_local_watchlist(tmp_path):
    cfg=Settings(TOKEN,OWNER,tmp_path/"db.sqlite",None,(),False,False,"FAKE_SECRET")
    with TestClient(create_app(cfg,screen_catalog=FakeTmdb())) as client:
        assert client.get("/api/screen/search?q=Interstellar").status_code==401
        assert client.get("/api/screen/search?q=Interstellar",headers=auth(888)).status_code==401
        found=client.get("/api/screen/search?q=Interstellar",headers=auth())
        assert found.status_code==200 and found.json()["results"][0]["playable"] is False
        assert "FAKE_SECRET" not in found.text
        assert client.post("/api/screen/watchlist/movie/157336",
               headers=auth(),json={"watch_status":"completed","score":9}).status_code==200
        assert client.post("/api/screen/watchlist/movie/157336",
               headers=auth(),json={"watch_status":"watching","season":2}).status_code==422
        saved=client.get("/api/screen/watchlist",headers=auth()).json()
        assert saved["results"][0]["score"]==9
        assert saved["results"][0]["playable"] is False
        assert client.delete("/api/screen/watchlist/movie/157336",
                             headers=auth()).json()["removed"]
