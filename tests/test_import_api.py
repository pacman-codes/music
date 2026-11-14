"""Owner-only import endpoint cannot become public download proxy."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from music_bot.api import create_app
from music_bot.catalog import CatalogTrack
from music_bot.config import Settings
from music_bot.importer import MediaImportError
from music_bot.storage import Library

TOKEN = "12345:fake-token"
OWNER = 178
RECORDING = "8a49dba0-253a-4535-b87f-78bb035336ce"


def init_data(user_id=OWNER):
    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": user_id}, separators=(",", ":"))}
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class StubImporter:
    def __init__(self):
        self.calls = []

    async def import_recording(self, mbid, url):
        self.calls.append((mbid, url))
        if "evil" in url:
            raise MediaImportError("Источник не разрешён")
        return {"track_id": 17, "reused": False, "sent": True}


def test_import_api_auth_and_config_guard(tmp_path):
    cfg = Settings(TOKEN, OWNER, tmp_path / "db.sqlite")
    body = {"url": "https://audio.example.org/tracks/song.mp3"}
    with TestClient(create_app(cfg)) as client:
        assert client.post(f"/api/discovery/import/{RECORDING}", json=body).status_code == 401
        hdr = {"X-Telegram-Init-Data": init_data()}
        assert client.get("/api/discovery/import-config", headers=hdr).json()["enabled"] is False
        assert client.post(f"/api/discovery/import/{RECORDING}",
                           json=body, headers=hdr).status_code == 409


def test_import_api_with_injected_service_and_owner(tmp_path):
    cfg = Settings(TOKEN, OWNER, tmp_path / "db.sqlite", None,
                   ("audio.example.org",))
    stub = StubImporter()
    lib = Library(cfg.db_path)
    lib.remember_candidates([CatalogTrack(RECORDING, "Roads", "Portishead")])
    with TestClient(create_app(cfg, importer=stub)) as client:
        bad = {"X-Telegram-Init-Data": init_data(999)}
        good = {"X-Telegram-Init-Data": init_data()}
        body = {"url": "https://audio.example.org/tracks/song.mp3"}
        assert client.post(f"/api/discovery/import/{RECORDING}",
                           json=body, headers=bad).status_code == 401
        assert client.post("/api/discovery/import/invalid",
                           json=body, headers=good).status_code == 422
        success = client.post(f"/api/discovery/import/{RECORDING}",
                              json=body, headers=good)
        assert success.status_code == 200 and success.json()["sent"] is True
        assert stub.calls == [(RECORDING, body["url"])]
        failure = client.post(
            f"/api/discovery/import/{RECORDING}",
            json={"url": "https://evil.example.org/file.mp3"}, headers=good,
        )
        assert failure.status_code == 422
        assert "не разрешён" in failure.json()["detail"]
