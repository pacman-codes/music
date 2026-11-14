"""Owner-only track radio prefers confirmed artist ID; never promotes metadata to files."""
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

OWNER=4242
TOKEN="1234:testing"
ARTIST="8f6bd1e4-fbe1-4f50-aa9b-94c450ec0f11"
A="8a49dba0-253a-4535-b87f-78bb035336ce"
B="f3bba4cd-8018-468b-902e-bc8f029593e5"
C="8e74dd9d-e5a3-4acd-918a-c36a0f8cda84"


def signed(user=OWNER):
    pairs={"auth_date": str(int(time.time())),
           "user":json.dumps({"id":user}, separators=(",",":"))}
    secret=hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    check="\n".join(f"{k}={v}" for k,v in sorted(pairs.items()))
    pairs["hash"]=hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


class RadioCatalog:
    def __init__(self):
        self.calls=[]

    async def search(self, text, limit=15, *, allow_variants=True):
        self.calls.append(("search",text))
        return [CatalogTrack(A, "Roads", "Portishead", artist_mbid=ARTIST),
                CatalogTrack(B, "Roads (Live)", "Portishead", artist_mbid=ARTIST,
                             variant=True)]

    async def artist_radio_by_id(self, mbid, *, difficulty, limit):
        self.calls.append(("id",mbid,difficulty,limit))
        return [CatalogTrack(A,"Roads","Portishead",artist_mbid=ARTIST),
                CatalogTrack(C,"Teardrop","Massive Attack")]

    async def artist_radio(self, name, *, difficulty, limit):
        self.calls.append(("name",name,difficulty,limit))
        return [CatalogTrack(C,"Teardrop","Massive Attack")]


def test_link_confirm_and_radio_owner_only(tmp_path):
    cfg=Settings(TOKEN, OWNER, tmp_path / "music.db")
    db=Library(cfg.db_path)
    song=db.add_track("tgfile","tgunique","Roads","Portishead")
    catalog=RadioCatalog()
    with TestClient(create_app(cfg,catalog=catalog)) as client:
        good={"X-Telegram-Init-Data":signed()}
        other={"X-Telegram-Init-Data":signed(user=8)}
        assert client.get(f"/api/tracks/{song.id}/radio").status_code==401
        assert client.post(
            f"/api/tracks/{song.id}/recording",
            headers=other,json={"mbid":A}).status_code==401

        unlinked=client.get(f"/api/tracks/{song.id}/radio",headers=good)
        assert unlinked.status_code==200
        assert catalog.calls[-1] == ("name","Portishead","medium",12)

        suggestions=client.get(
            f"/api/tracks/{song.id}/match-suggestions",headers=good)
        assert suggestions.status_code==200
        assert suggestions.json()["results"][0]["local_track_id"] is None
        assert suggestions.json()["results"][0]["possible_match_track_id"]==song.id

        assert client.post(
            f"/api/tracks/{song.id}/recording",
            headers=good,json={"mbid":"not-mbid"}).status_code==422
        assert client.post(
            f"/api/tracks/{song.id}/recording",
            headers=good,json={"mbid":A}).status_code==200
        linked=client.get(f"/api/tracks/{song.id}/recording",headers=good)
        assert linked.json()["recording"]["artist_mbid"]==ARTIST
        found=client.get("/api/discovery/search?q=Roads",headers=good).json()
        assert found["results"][0]["local_track_id"]==song.id
        assert found["results"][0]["match_confirmed"] is True

        radio=client.get(
            f"/api/tracks/{song.id}/radio?difficulty=hard",headers=good)
        assert radio.status_code==200
        assert catalog.calls[-1] == ("id",ARTIST,"hard",12)
        assert radio.json()["used_confirmed_artist_id"] is True
        assert radio.json()["results"][0]["title"]=="Teardrop"
        assert all(r["playable"] is False for r in radio.json()["results"])
        assert len(radio.json()["results"])==1  # Original already saved/linked.

        assert client.delete(f"/api/tracks/{song.id}/recording",
                             headers=good).json()["removed"] is True
        assert client.get(f"/api/tracks/{song.id}/recording",
                          headers=good).json()["recording"] is None
        assert client.get("/api/tracks/999999/radio",headers=good).status_code==404


def test_cannot_confirm_unknown_catalog_recording(tmp_path):
    cfg=Settings(TOKEN,OWNER,tmp_path/"music.db")
    db=Library(cfg.db_path)
    t=db.add_track("f","u","Song","Artist")
    with TestClient(create_app(cfg,catalog=RadioCatalog())) as client:
        hdr={"X-Telegram-Init-Data":signed()}
        result=client.post(
            f"/api/tracks/{t.id}/recording",headers=hdr,json={"mbid":A})
        assert result.status_code==409
