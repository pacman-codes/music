import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from music_bot.api import create_app
from music_bot.config import Settings
from music_bot.storage import Library
from music_bot.webapp_auth import InvalidInitData, verify_init_data


TOKEN = "1234:offline-test-only"
OWNER = 42


def signed_init_data(user_id=OWNER, timestamp=None, extra=None):
    data = {
        "auth_date": str(int(time.time()) if timestamp is None else timestamp),
        "user": json.dumps({"id": user_id, "first_name": "Tester"}, separators=(",", ":")),
        "query_id": "test-123",
    }
    data.update(extra or {})
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


def test_good_init_data_and_owner():
    raw = signed_init_data()
    assert verify_init_data(raw, TOKEN, OWNER)["id"] == OWNER


@pytest.mark.parametrize("bad", [
    lambda raw: raw + "&query_id=duplicate",
    lambda raw: raw.replace("Tester", "Attacker"),
    lambda raw: raw.replace("hash=", "hack="),
    lambda raw: "",
])
def test_init_data_rejects_invalid(bad):
    with pytest.raises(InvalidInitData):
        verify_init_data(bad(signed_init_data()), TOKEN, OWNER)


def test_init_data_rejects_other_user_or_old_session():
    with pytest.raises(InvalidInitData):
        verify_init_data(signed_init_data(user_id=100), TOKEN, OWNER)
    with pytest.raises(InvalidInitData):
        verify_init_data(signed_init_data(timestamp=int(time.time())-3700), TOKEN, OWNER)


def test_api_requires_valid_telegram_auth(tmp_path):
    cfg = Settings(TOKEN, OWNER, tmp_path / "music.db")
    app = create_app(cfg)
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/tracks").status_code == 401
        assert client.get(
            "/api/tracks", headers={"X-Telegram-Init-Data": signed_init_data(99)}
        ).status_code == 401
        headers = {"X-Telegram-Init-Data": signed_init_data()}
        assert client.get("/api/tracks", headers=headers).json() == []
        lib = Library(cfg.db_path)
        track = lib.add_track("f", "u", "Hello", "Artist")
        result = client.get("/api/tracks", headers=headers)
        assert result.status_code == 200
        assert result.json()[0]["id"] == track.id
        assert "file_id" not in result.text
        liked = client.post(f"/api/like/{track.id}", headers=headers)
        assert liked.status_code == 200
        assert liked.json()["changed"]
        playlist = client.post("/api/playlists", headers=headers, json={"name": "Road"})
        assert playlist.status_code == 201
        playlist_id = playlist.json()["id"]
        added = client.post(
            f"/api/playlists/{playlist_id}/add",
            headers=headers,
            json={"track_id": track.id},
        )
        assert added.json()["changed"]
        tracks = client.get(f"/api/playlists/{playlist_id}/tracks", headers=headers)
        assert tracks.json()[0]["title"] == "Hello"
