"""Owner-only album collection shared between the bot and Mini App."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from music_bot.api import create_app
from music_bot.catalog import CatalogError
from music_bot.config import Settings
from music_bot.storage import Library

TOKEN = "1234:test"
OWNER = 42
RELEASE = "8a49dba0-253a-4535-b87f-78bb035336ce"
FIRST = "f3bba4cd-8018-468b-902e-bc8f029593e5"
SECOND = "8e74dd9d-e5a3-4acd-918a-c36a0f8cda84"


def auth(user=OWNER):
    data = {"auth_date": str(int(time.time())),
            "user": json.dumps({"id": user}, separators=(",", ":"))}
    key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    data["hash"] = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(data)}


class Catalog:
    async def _json(self, provider, path, params, **kwargs):
        assert provider == "mb"
        credit = [{"name": "Artist"}]
        release = {"id": RELEASE, "title": "Album", "artist-credit": credit,
                   "release-group": {"id": RELEASE, "primary-type": "Album"},
                   "track-count": 2, "status": "Official"}
        if path == "/ws/2/release/":
            return {"releases": [release]}
        if path == "/ws/2/release/" + RELEASE:
            return {**release, "media": [{"tracks": [
                {"title": "First", "recording": {"id": FIRST}},
                {"title": "Second", "recording": {"id": SECOND}},
            ]}]}
        raise CatalogError("Missing")


def test_album_api_shares_collection_and_does_not_fake_audio(tmp_path):
    cfg = Settings(TOKEN, OWNER, tmp_path / "music.db")
    lib = Library(cfg.db_path)
    with TestClient(create_app(cfg, catalog=Catalog())) as client:
        for path in ("/api/albums", "/api/albums/search?q=Artist+Album",
                     "/api/albums/" + RELEASE):
            assert client.get(path).status_code == 401
            assert client.get(path, headers=auth(999)).status_code == 401
        hdr = auth()
        path = "/api/albums/" + RELEASE
        assert client.post(path).status_code == 401
        assert client.delete(path).status_code == 401
        assert client.post(path + "/play").status_code == 401
        found = client.get("/api/albums/search?q=Artist+Album", headers=hdr)
        assert found.status_code == 200
        assert found.json()["results"][0]["mbid"] == RELEASE
        assert not found.json()["results"][0]["saved"]
        assert client.post(path, headers=hdr).json()["changed"]
        assert not client.post(path, headers=hdr).json()["changed"]
        assert lib.get_saved_album(RELEASE)["title"] == "Album"
        detail = client.get(path, headers=hdr)
        assert detail.status_code == 200
        row = detail.json()
        assert row["saved"] and not row["playable"]
        assert [x["title"] for x in row["tracks"]] == ["First", "Second"]
        assert all(x["local_track_id"] is None for x in row["tracks"])
        assert "file_id" not in detail.text
        assert client.post(path + "/play", headers=hdr).status_code == 409
        assert client.delete(path, headers=hdr).json()["removed"]
        assert lib.get_saved_album(RELEASE) is None
        lib.save_album(RELEASE, "Album", "Artist", 2)
        assert client.get("/api/albums", headers=hdr).json()["results"][0]["mbid"] == RELEASE
        assert client.get("/api/albums/invalid", headers=hdr).status_code == 422
        assert client.get("/api/albums?offset=-1", headers=hdr).status_code == 422
