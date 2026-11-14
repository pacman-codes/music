"""Playing a single saved song uses Telegram file_id without downloading media."""
import asyncio
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendAudio
from fastapi.testclient import TestClient

import music_bot.api as api_module
from music_bot.api import create_app
from music_bot.config import Settings
from music_bot.main import send_single_track, track_keyboard
from music_bot.storage import Library

TOKEN = "123456:fake-token"
OWNER = 9876


class RecordingBot:
    audio_calls = []
    failure = False

    def __init__(self, token=None):
        assert token is None or token == TOKEN

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def send_audio(self, *args, **kwargs):
        if self.failure:
            raise TelegramBadRequest(
                method=SendAudio(chat_id=OWNER, audio="invalid-file-id"),
                message="Bad Request: wrong file identifier",
            )
        self.audio_calls.append((args, kwargs))


def signed(user=OWNER):
    data = {"auth_date": str(int(time.time())),
            "user": json.dumps({"id": user}, separators=(",", ":"))}
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{k}={v}" for k,v in sorted(data.items()))
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(data)}


def test_single_send_is_native_and_tracks_only_delivery(tmp_path):
    db = Library(tmp_path / "db.sqlite")
    track = db.add_track("tg-id", "unique", "Roads", "Portishead")
    bot = RecordingBot()
    RecordingBot.audio_calls = []
    RecordingBot.failure = False
    got = asyncio.run(send_single_track(bot, db, OWNER, track.id))
    assert got.id == track.id
    assert RecordingBot.audio_calls[0][1]["audio"] == "tg-id"
    assert RecordingBot.audio_calls[0][1]["title"] == "Roads"
    markup = RecordingBot.audio_calls[0][1]["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == f"play:{track.id}"
    assert track.id in db.recent_ids()
    assert db.playback_stats().get(track.id) is None  # not actual playback
    with pytest.raises(ValueError):
        asyncio.run(send_single_track(bot, db, OWNER, 99999))
    assert len(RecordingBot.audio_calls) == 1


def test_failed_telegram_send_never_marks_track_delivered(tmp_path):
    db = Library(tmp_path / "db.sqlite")
    track = db.add_track("tg-id", "unique", "Roads", "Portishead")
    RecordingBot.audio_calls = []
    RecordingBot.failure = True
    try:
        with pytest.raises(TelegramBadRequest):
            asyncio.run(send_single_track(RecordingBot(), db, OWNER, track.id))
        assert db.recent_ids() == []
    finally:
        RecordingBot.failure = False


def test_play_endpoint_owner_only_and_playback_failures(tmp_path, monkeypatch):
    cfg = Settings(TOKEN, OWNER, tmp_path / "db.sqlite")
    track = Library(cfg.db_path).add_track(
        "tg-id", "unique", "Roads", "Portishead"
    )
    RecordingBot.audio_calls = []
    RecordingBot.failure = False
    monkeypatch.setattr(api_module, "Bot", RecordingBot)

    with TestClient(create_app(cfg)) as client:
        url = f"/api/tracks/{track.id}/play"
        assert client.post(url).status_code == 401
        assert client.post(url, headers=signed(13)).status_code == 401
        assert client.post("/api/tracks/999/play", headers=signed()).status_code == 404
        accepted = client.post(url, headers=signed())
        assert accepted.status_code == 200
        assert accepted.json()["native_player"] is True
        assert accepted.json()["track_id"] == track.id
        assert len(RecordingBot.audio_calls) == 1
        RecordingBot.failure = True
        try:
            failed = client.post(url, headers=signed())
            assert failed.status_code == 502
        finally:
            RecordingBot.failure = False
        assert len(RecordingBot.audio_calls) == 1
        assert Library(cfg.db_path).recent_ids()[0] == track.id
