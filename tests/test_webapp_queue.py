"""Mini App queue-to-native-Telegram test, networkless fake Bot."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from fastapi.testclient import TestClient

import music_bot.api as api
from music_bot.config import Settings
from music_bot.storage import Library


TOKEN = "1234:test-token"
OWNER = 8271


def signed():
    info = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": OWNER}, separators=(",", ":")),
    }
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{k}={v}" for k, v in sorted(info.items()))
    info["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(info)


class FakeTelegramBot:
    def __init__(self, token):
        assert token == TOKEN
        self.groups = []
        self.messages = []
        self.single = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def send_audio(self, *args, **kwargs):
        self.single.append((args, kwargs))

    async def send_media_group(self, *args, **kwargs):
        self.groups.append((args, kwargs))

    async def send_message(self, *args, **kwargs):
        self.messages.append((args, kwargs))


def test_minapp_dispatches_native_audio_queue_and_requires_auth(tmp_path, monkeypatch):
    fake = FakeTelegramBot(TOKEN)
    monkeypatch.setattr(api, "Bot", lambda token: fake)
    settings = Settings(TOKEN, OWNER, tmp_path / "music.db")
    db = Library(settings.db_path)
    for i in range(12):
        db.add_track(f"f{i}", f"u{i}", f"Song {i}", "Band")
    app = api.create_app(settings)
    with TestClient(app) as client:
        assert client.post("/api/queue", json={"kind": "wave"}).status_code == 401
        hdr = {"X-Telegram-Init-Data": signed()}
        response = client.post("/api/queue", json={"kind": "wave"}, headers=hdr)
        assert response.status_code == 200
        assert response.json()["sent"] == 10
        assert len(fake.groups) == 1
        assert len(fake.groups[0][1]["media"]) == 10
        assert len(db.recent_ids()) == 10
        assert any("more:" in str(m) for m in fake.messages)
