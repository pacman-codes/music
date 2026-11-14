"""Authenticated Mini App decision lists and real Telegram queue integration."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from fastapi.testclient import TestClient

import music_bot.api as api
from music_bot.catalog import CatalogTrack
from music_bot.config import Settings
from music_bot.storage import Library


TOKEN = "123:offline-five-verdict"
OWNER = 911
MBID = "8a49dba0-253a-4535-b87f-78bb035336ce"


def signed(owner=OWNER):
    data = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": owner}, separators=(",", ":")),
    }
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(data)}


class FakeTelegramBot:
    sent = []
    def __init__(self, token):
        assert token == TOKEN

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def send_audio(self, *args, **kwargs):
        self.sent.append((args, kwargs))

    async def send_message(self, *args, **kwargs):
        return None


def test_five_way_api_auth_and_metadata_only_play_links(tmp_path, monkeypatch):
    fake = FakeTelegramBot(TOKEN)
    monkeypatch.setattr(api, "Bot", lambda token: fake)
    db_path = tmp_path / "music.db"
    db = Library(db_path)
    track = db.add_track("tg_audio_PRIVATE", "uid_PRIVATE", "Local", "Owner")
    db.remember_candidates([CatalogTrack(MBID, "Crowded Roomz", "Nia Archives")])
    app = api.create_app(Settings(TOKEN, OWNER, db_path))
    with TestClient(app) as client:
        assert client.get("/api/decisions").status_code == 401
        assert client.get("/api/decisions", headers=signed(999)).status_code == 401
        bad = client.post(f"/api/decisions/local/{track.id}", json={"status": "add"})
        assert bad.status_code == 401
        mine = signed()
        categories = client.get("/api/decisions", headers=mine)
        assert categories.status_code == 200
        assert len(categories.json()) == 5

        post = client.post(f"/api/decisions/local/{track.id}", headers=mine,
                           json={"status": "add"})
        assert post.status_code == 200
        assert track.id in db.liked_ids()
        by_status = client.get("/api/decisions/add", headers=mine)
        assert by_status.status_code == 200
        local = by_status.json()["items"][0]
        assert local["track_id"] == track.id
        assert local["playable"] is True
        assert "tg_audio_PRIVATE" not in by_status.text
        assert "uid_PRIVATE" not in by_status.text

        external = client.post("/api/decisions/catalog/" + MBID, headers=mine,
                               json={"status": "interesting"})
        assert external.status_code == 200
        view = client.get("/api/decisions/interesting", headers=mine).json()
        assert view["total"] == 1
        item = view["items"][0]
        assert item["playable"] is False
        assert item["track_id"] is None
        assert item["yandex_search_url"].startswith("https://music.yandex.ru/search?")
        assert client.post("/api/decisions/interesting/play",
                           headers=mine).status_code == 409

        result = client.post("/api/decisions/add/play", headers=mine)
        assert result.status_code == 200
        assert result.json()["sent"] == 1
        assert fake.sent[-1][1]["audio"] == "tg_audio_PRIVATE"

        assert client.post(f"/api/decisions/local/{track.id}",headers=mine,
                           json={"status": "bad"}).status_code == 422
        assert client.get("/api/decisions/unknown",headers=mine).status_code == 404
        assert client.get("/api/decisions/add?limit=0",headers=mine).status_code == 422
        assert client.post("/api/decisions/catalog/not-uuid",headers=mine,
                           json={"status":"add"}).status_code == 422
        assert client.post("/api/decisions/local/999999",headers=mine,
                           json={"status":"add"}).status_code == 404


def test_decisions_auth_applies_to_audio_queue(tmp_path, monkeypatch):
    calls = []
    class FailIfCalled:
        def __init__(self, *args, **kwargs):
            calls.append(True)

    monkeypatch.setattr(api, "Bot", FailIfCalled)
    db = Library(tmp_path / "db.sqlite")
    t = db.add_track("file", "uid", "Track", "Artist")
    db.set_track_verdict(t.id, "maybe")
    with TestClient(api.create_app(Settings(TOKEN, OWNER, db.path))) as client:
        assert client.post("/api/decisions/maybe/play").status_code == 401
        assert client.post("/api/decisions/maybe/play", headers=signed(999)).status_code == 401
        assert not calls
