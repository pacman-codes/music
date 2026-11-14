"""A MusicBrainz tag update must be explicitly confirmed by the owner."""
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

TOKEN="1234:fake"
OWNER=42
MBID="8a49dba0-253a-4535-b87f-78bb035336ce"


def owner_header(user=OWNER):
    info={"auth_date":str(int(time.time())),
          "user":json.dumps({"id":user},separators=(",",":"))}
    secret=hmac.new(b"WebAppData",TOKEN.encode(),hashlib.sha256).digest()
    check="\n".join(f"{x}={y}" for x,y in sorted(info.items()))
    info["hash"]=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data":urlencode(info)}


def test_apply_local_tags_is_owner_only_and_requires_confirmed_identity(tmp_path):
    config=Settings(TOKEN,OWNER,tmp_path/"music.db")
    db=Library(config.db_path)
    t=db.add_track("tg-file","tg-unique","Bad title","Bad Artist")
    db.remember_candidates([CatalogTrack(MBID,"Roads","Portishead")])
    path=f"/api/tracks/{t.id}/apply-recording-tags"
    with TestClient(create_app(config)) as client:
        assert client.post(path).status_code==401
        assert client.post(path,headers=owner_header(123)).status_code==401
        assert client.post(path,headers=owner_header()).status_code==409
        db.confirm_recording_match(t.id,MBID)
        applied=client.post(path,headers=owner_header())
        assert applied.status_code==200
        payload=applied.json()
        assert payload["title"]=="Roads"
        assert payload["artist"]=="Portishead"
        assert payload["embedded_telegram_tags_changed"] is False
        assert db.get_track(t.id).file_id=="tg-file"
        assert client.post("/api/tracks/999/apply-recording-tags",
                           headers=owner_header()).status_code==409
