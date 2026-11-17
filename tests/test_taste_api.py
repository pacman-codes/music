"""Mini App public-source import must always enforce Telegram owner auth."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode
from pathlib import Path

from fastapi.testclient import TestClient

import music_bot.api as module
from music_bot.config import Settings
from music_bot.storage import Library
from music_bot.taste_sources import SourceSong


def signature(token="123:source-test", owner=411):
    body = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id":owner}, separators=(",",":")),
    }
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{key}={value}" for key,value in sorted(body.items()))
    body["hash"] = hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(body)}


def test_authenticated_external_likes_api_yandex_refresh_and_youtube_import(tmp_path,monkeypatch):
    db = Library(tmp_path / "music.db")
    url = "https://music.yandex.ru/playlists/lk.aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    async def fake_fetch(value):
        assert value == url
        return [
            SourceSong("1","New Song","A Band","https://music.yandex.ru/track/1")
        ]
    monkeypatch.setattr(module,"fetch_yandex_playlist",fake_fetch)
    app = module.create_app(Settings("123:source-test", 411, db.path))
    with TestClient(app) as client:
        assert client.get("/api/sources").status_code == 401
        assert client.get("/api/sources",headers=signature(owner=99)).status_code == 401
        assert client.post("/api/sources/yandex/sync",json={"url":url}).status_code == 401
        headers=signature()
        result=client.post("/api/sources/yandex/sync",json={"url":url},headers=headers)
        assert result.status_code==200, result.text
        assert result.json()["active"]==1
        second=client.post("/api/sources/yandex/refresh",headers=headers)
        assert second.status_code==200 and second.json()["added"]==0
        assert len(client.get("/api/sources",headers=headers).json())==1
        assert client.get("/api/sources/yandex/tracks",headers=headers).json()["total"]==1
        assert client.get("/api/sources/yandex/tracks?offset=1",headers=headers).json()["items"]==[]
        assert client.get("/api/sources/unknown/tracks",headers=headers).status_code==404
        assert client.post(
            "/api/sources/yandex/sync",headers=headers,
            json={"url":"https://localhost:8000/unsafe"},
        ).status_code==422

        copied_page="YouTube Music\nLiked songs\nSample\nBand\nAlbum\n3:24\n"
        saved=client.post(
            "/api/sources/youtube_music/import",headers=headers,
            json={"filename":"liked.txt","content":copied_page},
        )
        assert saved.status_code==200, saved.text
        assert saved.json()["active"]==1
        # Source metadata must not add imaginary local playable Telegram files.
        assert db.all_tracks()==[] and db.liked_ids()==set()
        assert client.get("/api/sources/youtube_music/tracks",headers=headers).json()["total"]==1
        assert client.post(
            "/api/sources/youtube_music/import",headers=signature(owner=99),
            json={"filename":"liked.txt","content":copied_page},
        ).status_code==401
        assert client.post(
            "/api/sources/youtube_music/import",headers=headers,
            json={"filename":"liked.csv","content":"Video ID\nabcdefghijk\n"},
        ).status_code==422


def test_source_mini_app_file_picker_buttons_and_owner_only_routes():
    root=Path(__file__).parent.parent
    html=(root / "web/index.html").read_text(encoding="utf-8")
    js=(root / "web/sources.js").read_text(encoding="utf-8")
    for ident in ("source-yandex-url","source-yandex-sync","source-yandex-repeat",
                  "source-youtube-data","source-youtube-file","source-youtube-sync",
                  "source-items","source-prev","source-next","source-status"):
        assert 'id="'+ident+'"' in html
        assert ident in js
    assert "/assets/sources.js" in html
    assert "X-Telegram-Init-Data" in (root / "web/app.js").read_text(encoding="utf-8")
