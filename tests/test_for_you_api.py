"""Personal discovery endpoint must be owner-only and metadata-only."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from music_bot.api import create_app
from music_bot.catalog import CatalogTrack
from music_bot.config import Settings
from music_bot.storage import Library

TOKEN="1234:local-test"
OWNER=42
A="8a49dba0-253a-4535-b87f-78bb035336ce"


def auth(owner=OWNER):
    info={"auth_date":str(int(time.time())),
          "user":json.dumps({"id":owner},separators=(",",":"))}
    key=hmac.new(b"WebAppData",TOKEN.encode(),hashlib.sha256).digest()
    check="\n".join(f"{x}={y}" for x,y in sorted(info.items()))
    info["hash"]=hmac.new(key,check.encode(),hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data":urlencode(info)}


class Catalog:
    def __init__(self):
        self.called=[]
    async def artist_radio(self,artist,*,difficulty,limit):
        self.called.append((artist,difficulty,limit))
        return [CatalogTrack(A,"Roads","Portishead")]


def test_for_you_favorites_and_auth(tmp_path):
    settings=Settings(TOKEN,OWNER,tmp_path/"music.db")
    catalog=Catalog()
    db=Library(settings.db_path)
    with TestClient(create_app(settings,catalog=catalog)) as client:
        assert client.get("/api/discovery/for-you").status_code==401
        assert client.get("/api/discovery/for-you",headers=auth(999)).status_code==401
        assert client.get("/api/discovery/for-you",headers=auth()).status_code==409
        t=db.add_track("file","uid","My Song","Portishead")
        db.set_rating(t.id,10)
        response=client.get("/api/discovery/for-you?difficulty=hard",headers=auth())
        assert response.status_code==200
        payload=response.json()
        assert payload["type"]=="metadata_only"
        assert payload["seed_artists"]==["Portishead"]
        assert payload["results"][0]["playable"] is False
        assert "file_id" not in response.text
        assert catalog.called==[("Portishead","hard",15)]
        assert client.post(f"/api/discovery/wishlist/{A}",headers=auth()).status_code==200
