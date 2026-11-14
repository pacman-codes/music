"""Offline end-to-end contract: search -> uploaded native audio -> SQLite -> Wave.

Only Telegram and yt-dlp are simulated. Route/auth, SQLite asset links,
real /api/queue selection and actual Bot API method signatures are exercised.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

from aiogram.types import FSInputFile
from fastapi.testclient import TestClient

import music_bot.api as api_module
import music_bot.youtube as youtube_module
from music_bot.api import create_app
from music_bot.config import Settings
from music_bot.storage import Library
from music_bot.youtube import VideoCandidate

TOKEN = "12345:local-test-bot"
OWNER = 4242
VIDEO = "dQw4w9WgXcQ"


def signed_init_data(user=OWNER):
    data = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": user}, separators=(",", ":")),
    }
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(data)}


class FakeSearch:
    def __init__(self):
        self.calls = []

    async def search(self, query, *, limit):
        self.calls.append((query, limit))
        return [VideoCandidate(VIDEO, "Roads", "Portishead", 302)]


class BotRecorder:
    audio = []
    media_groups = []
    messages = []
    paths = []
    invalid_uploads = False

    def __init__(self, token):
        assert token == TOKEN

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def send_audio(self, *args, **kwargs):
        audio = kwargs["audio"]
        self.audio.append(audio)
        if isinstance(audio, FSInputFile):
            path = Path(audio.path)
            assert path.is_file()
            assert path.read_bytes().startswith(b"ID3")
            self.paths.append(path)
            if self.invalid_uploads:
                return SimpleNamespace(audio=None)
            return SimpleNamespace(audio=SimpleNamespace(
                file_id="telegram-file-id-123",
                file_unique_id="telegram-unique-id-123",
            ))
        assert audio == "telegram-file-id-123"
        return SimpleNamespace(audio=SimpleNamespace(
            file_id=audio, file_unique_id="telegram-unique-id-123"
        ))

    async def send_media_group(self, *args, **kwargs):
        self.media_groups.append((args, kwargs))
        return []

    async def send_message(self, *args, **kwargs):
        self.messages.append((args, kwargs))
        return SimpleNamespace(message_id=len(self.messages))


def test_search_download_and_native_telegram_playback(tmp_path, monkeypatch):
    BotRecorder.audio = []
    BotRecorder.media_groups = []
    BotRecorder.messages = []
    BotRecorder.paths = []
    BotRecorder.invalid_uploads = False
    source = FakeSearch()
    config = Settings(TOKEN, OWNER, tmp_path / "music.db", None, (), True, True)
    monkeypatch.setattr(api_module, "Bot", BotRecorder)

    @asynccontextmanager
    async def fake_mp3(video_id):
        assert video_id == VIDEO
        path = tmp_path / "temporary-download.mp3"
        path.write_bytes(b"ID3" + b"\x00" * 500)
        try:
            yield path
        finally:
            path.unlink(missing_ok=True)

    monkeypatch.setattr(youtube_module, "temporary_youtube_mp3", fake_mp3)
    with TestClient(create_app(config, youtube_source=source)) as client:
        auth = signed_init_data()
        result = client.get("/api/youtube/search?q=Portishead%20Roads", headers=auth)
        assert result.status_code == 200
        record = result.json()["results"][0]
        assert record["video_id"] == VIDEO
        assert record["track_id"] is None
        assert record["playable"] is False
        assert source.calls == [("Portishead Roads", 6)]
        assert client.post(f"/api/youtube/import/{VIDEO}").status_code == 401

        imported = client.post(f"/api/youtube/import/{VIDEO}", headers=auth)
        assert imported.status_code == 200, imported.text
        assert imported.json()["reused"] is False
        track_id = imported.json()["track_id"]
        assert isinstance(BotRecorder.audio[0], FSInputFile)
        assert not BotRecorder.paths[0].exists()  # downloaded bytes gone

        db = Library(config.db_path)
        track = db.get_track(track_id)
        assert track is not None
        assert track.file_id == "telegram-file-id-123"
        assert db.get_youtube_video(VIDEO)["track_id"] == track_id

        repeated = client.post(f"/api/youtube/import/{VIDEO}", headers=auth)
        assert repeated.status_code == 200
        assert repeated.json()["reused"] is True
        assert BotRecorder.audio[1] == track.file_id
        assert len(BotRecorder.paths) == 1  # No second download

        # The imported file must be selectable by the normal recommendation
        # engine and sent to the same native Telegram audio player.
        rating = client.post(
            f"/api/rating/{track_id}", headers=auth, json={"score": 9}
        )
        assert rating.status_code == 200
        queued = client.post(
            "/api/queue", headers=auth, json={"kind": "wave"}
        )
        assert queued.status_code == 200, queued.text
        assert queued.json()["sent"] == 1
        assert BotRecorder.audio[2] == track.file_id
        assert len(BotRecorder.media_groups) == 0
        assert BotRecorder.messages[-1][1]["reply_markup"]
        assert db.ratings()[track_id] == 9
        assert db.peek_queue(queued.json()["queue_id"]).cursor == 1


def test_failed_telegram_upload_keeps_library_empty(tmp_path, monkeypatch):
    BotRecorder.audio = []
    BotRecorder.media_groups = []
    BotRecorder.messages = []
    BotRecorder.paths = []
    BotRecorder.invalid_uploads = True
    monkeypatch.setattr(api_module, "Bot", BotRecorder)

    @asynccontextmanager
    async def fake_mp3(_):
        file = tmp_path / "test.mp3"
        file.write_bytes(b"ID3" + b"\x00" * 30)
        try:
            yield file
        finally:
            file.unlink(missing_ok=True)

    monkeypatch.setattr(youtube_module, "temporary_youtube_mp3", fake_mp3)
    config = Settings(TOKEN, OWNER, tmp_path / "music.db", None, (), True, True)
    with TestClient(create_app(config, youtube_source=FakeSearch())) as client:
        auth = signed_init_data()
        assert client.get("/api/youtube/search?q=Roads", headers=auth).status_code == 200
        failed = client.post(f"/api/youtube/import/{VIDEO}", headers=auth)
        assert failed.status_code == 502, failed.text
        db = Library(config.db_path)
        assert db.all_tracks() == []
        assert db.get_youtube_video(VIDEO)["track_id"] is None
        assert not BotRecorder.paths[0].exists()
