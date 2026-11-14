"""Owner-only API can suggest, but never silently import, YouTube for a MBID."""
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
from music_bot.youtube import VideoCandidate

TOKEN="1234:test"
OWNER=42
MBID="8a49dba0-253a-4535-b87f-78bb035336ce"
VIDEO="dQw4w9WgXcQ"


def auth(user=OWNER):
    fields={"auth_date":str(int(time.time())),
            "user":json.dumps({"id":user},separators=(",",":"))}
    key=hmac.new(b"WebAppData",TOKEN.encode(),hashlib.sha256).digest()
    check="\n".join(f"{k}={v}" for k,v in sorted(fields.items()))
    fields["hash"]=hmac.new(key,check.encode(),hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data":urlencode(fields)}


class StubYouTube:
    def __init__(self):
        self.queries=[]
    async def search(self,q,*,limit):
        self.queries.append((q,limit))
        return [VideoCandidate(VIDEO,"Portishead - Roads","Portishead",300)]


def test_recording_suggestions_require_owner_and_known_mbid(tmp_path):
    cfg=Settings(TOKEN,OWNER,tmp_path/"music.db",None,(),True,False)
    db=Library(cfg.db_path)
    db.remember_candidates([CatalogTrack(MBID,"Roads","Portishead",
                                          duration_ms=300000)])
    stub=StubYouTube()
    with TestClient(create_app(cfg,youtube_source=stub)) as client:
        url=f"/api/discovery/{MBID}/youtube-suggestions"
        assert client.get(url).status_code==401
        assert client.get(url,headers=auth(888)).status_code==401
        unknown=client.get(
            "/api/discovery/00000000-0000-0000-0000-000000000000/youtube-suggestions",
            headers=auth())
        assert unknown.status_code==404
        response=client.get(url,headers=auth())
        assert response.status_code==200
        obj=response.json()
        assert obj["auto_imported"] is False
        assert obj["suggestions"][0]["video_id"] == VIDEO
        assert obj["suggestions"][0]["playable"] is False
        assert "file_id" not in response.text
        assert stub.queries==[("Portishead Roads",8)]
        # Merely suggesting a video must not create Telegram audio files.
        assert db.all_tracks()==[]
        assert db.get_youtube_video(VIDEO)["track_id"] is None


def test_disabled_youtube_suggestions_do_not_query_service(tmp_path):
    cfg=Settings(TOKEN,OWNER,tmp_path/"music.db")
    with TestClient(create_app(cfg,youtube_source=StubYouTube())) as client:
        assert client.get(
            f"/api/discovery/{MBID}/youtube-suggestions",
            headers=auth(),
        ).status_code==409
