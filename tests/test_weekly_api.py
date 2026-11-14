"""Owner can view weekly metadata without granting access to anyone else."""
import hashlib,hmac,json,time
from urllib.parse import urlencode
from fastapi.testclient import TestClient

from music_bot.api import create_app
from music_bot.config import Settings
from music_bot.storage import Library
from music_bot.catalog import CatalogTrack

TOKEN="12345:fake"
OWNER=42
RELEASE="8a49dba0-253a-4535-b87f-78bb035336ce"
TRACK="f3bba4cd-8018-468b-902e-bc8f029593e5"


def auth(owner=OWNER):
    fields={"auth_date":str(int(time.time())),
            "user":json.dumps({"id":owner},separators=(",",":"))}
    key=hmac.new(b"WebAppData",TOKEN.encode(),hashlib.sha256).digest()
    payload="\n".join(f"{k}={v}" for k,v in sorted(fields.items()))
    fields["hash"]=hmac.new(key,payload.encode(),hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data":urlencode(fields)}


class Catalog:
    async def _json(self,provider,path,params,ttl):
        return {"payload":{"releases":[{
            "release_group_mbid":RELEASE,
            "release_name":"New Album",
            "artist_credit_name":"Portishead",
            "release_date":__import__("datetime").date.today().isoformat(),
            "release_group_primary_type":"Album",
        }]}}
    async def artist_radio(self,artist,*,difficulty,limit):
        return [CatalogTrack(TRACK,"New Song","Other Artist")]


def test_weekly_route_requires_owner_and_stores_only_song_candidates(tmp_path):
    cfg=Settings(TOKEN,OWNER,tmp_path/"music.db")
    db=Library(cfg.db_path)
    track=db.add_track("file","unique","Roads","Portishead")
    db.like(track.id)
    with TestClient(create_app(cfg,catalog=Catalog())) as client:
        assert client.get("/api/discovery/weekly").status_code==401
        assert client.get("/api/discovery/weekly",headers=auth(333)).status_code==401
        reply=client.get("/api/discovery/weekly",headers=auth())
        assert reply.status_code==200,reply.text
        data=reply.json()
        assert data["type"]=="metadata_only"
        assert data["releases"][0]["playable"] is False
        assert data["tracks"][0]["playable"] is False
        assert db.get_candidate(TRACK)["mbid"]==TRACK
        assert db.get_candidate(RELEASE) is None
