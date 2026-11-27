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


def test_playlist_detail_edit_and_play_from_track(tmp_path, monkeypatch):
    fake = FakeTelegramBot(TOKEN)
    monkeypatch.setattr(api, "Bot", lambda token: fake)
    settings = Settings(TOKEN, OWNER, tmp_path / "music.db")
    db = Library(settings.db_path)
    pid = db.create_playlist("Night")
    tracks = [db.add_track(f"f{i}", f"u{i}", f"Song {i}", "Band") for i in range(23)]
    for track in tracks:
        db.add_to(pid, track.id)
    path = f"/api/playlists/{pid}"
    hdr = {"X-Telegram-Init-Data": signed()}
    with TestClient(api.create_app(settings)) as client:
        for method, suffix, payload in [
            ("GET", "", None), ("PUT", "", {"name":"Changed"}),
            ("DELETE", "", None), ("DELETE", f"/tracks/{tracks[0].id}", None),
            ("PUT", f"/tracks/{tracks[0].id}/position", {"delta":1}),
            ("POST", "/play", {})]:
            assert client.request(method, path+suffix, json=payload).status_code == 401
        detail = client.get(path+"?page=1", headers=hdr).json()
        assert detail["total"] == 23 and detail["pages"] == 2
        assert [t["id"] for t in detail["tracks"]] == [t.id for t in tracks[20:]]
        assert "file_id" not in str(detail)
        assert client.put(path, headers=hdr, json={"name":"Renamed"}).status_code == 200
        assert db.get_playlist(pid)["name"] == "Renamed"
        assert client.put(path+f"/tracks/{tracks[0].id}/position",headers=hdr,json={"delta":1}).json()["changed"]
        assert [t.id for t in db.playlist_tracks(pid)[:2]] == [tracks[1].id,tracks[0].id]
        assert client.delete(path+f"/tracks/{tracks[0].id}",headers=hdr).json()["changed"]
        assert db.get_track(tracks[0].id) is not None
        assert client.post(path+"/play",headers=hdr,json={"start_track_id":tracks[0].id}).status_code == 409
        assert client.post(path+"/play",headers=hdr,json={"shuffle":True,"start_track_id":tracks[20].id}).status_code == 422
        played = client.post(path+"/play",headers=hdr,json={"start_track_id":tracks[20].id})
        assert played.status_code == 200 and played.json()["sent"] == 3
        assert [m.media for m in fake.groups[-1][1]["media"]] == ["f20","f21","f22"]
        assert [t.id for t in db.delivered_queue_tracks(played.json()["queue_id"])] == [t.id for t in tracks[20:]]
        shuffled = client.post(path+"/play",headers=hdr,json={"shuffle":True})
        assert shuffled.status_code == 200 and shuffled.json()["sent"] == 10
        with db.con() as con:
            ids = [r[0] for r in con.execute("SELECT track_id FROM queue_items WHERE queue_id=?", (shuffled.json()["queue_id"],))]
        assert sorted(ids) == sorted(t.id for t in tracks[1:])
        assert client.delete(f"/api/playlists/{db.liked_id()}",headers=hdr).status_code == 409
        assert client.put(f"/api/playlists/{db.liked_id()}",headers=hdr,json={"name":"New"}).status_code == 409
        assert client.delete(path,headers=hdr).status_code == 200
        assert len(db.all_tracks()) == 23
        assert client.get(path,headers=hdr).status_code == 404


def test_history_and_smart_api_share_library_and_replay_only_delivered(tmp_path, monkeypatch):
    fake = FakeTelegramBot(TOKEN)
    monkeypatch.setattr(api, "Bot", lambda token: fake)
    cfg = Settings(TOKEN, OWNER, tmp_path / "music.db")
    db = Library(cfg.db_path)
    tracks = [db.add_track(f"f{i}", f"u{i}", f"Song {i}", "Band") for i in range(24)]
    db.set_rating(tracks[0].id, 9)
    db.set_rating(tracks[1].id, 8)
    db.set_track_verdict(tracks[1].id, "later")
    qid = db.create_queue([t.id for t in tracks], "Original", "playlist")
    db.mark_queue_sent(db.peek_queue(qid))
    for t in tracks[10:]:
        db.record_single_send(t.id)
    hdr = {"X-Telegram-Init-Data": signed()}
    with TestClient(api.create_app(cfg)) as client:
        for method,path in [
            ("GET","/api/history"),("GET","/api/history?mode=queues"),
            ("POST",f"/api/history/queues/{qid}/replay"),
            ("GET","/api/smart-playlists"),("GET","/api/smart-playlists/top"),
            ("POST","/api/smart-playlists/top/play")]:
            assert client.request(method,path).status_code == 401
        first = client.get("/api/history",headers=hdr).json()
        assert len(first["results"]) == 20
        assert first["results"][0]["track_id"] == tracks[-1].id
        second = client.get("/api/history?before="+str(first["next_before"]),headers=hdr).json()
        assert len(second["results"]) == 4 and second["next_before"] is None
        assert "file_id" not in str(first)
        queues = client.get("/api/history?mode=queues",headers=hdr).json()["results"]
        assert queues[0]["queue_id"] == qid and queues[0]["delivered_count"] == 10
        replay = client.post(f"/api/history/queues/{qid}/replay",headers=hdr)
        assert replay.status_code == 200 and replay.json()["sent"] == 10
        assert [t.id for t in db.delivered_queue_tracks(replay.json()["queue_id"])] == [t.id for t in tracks[:10]]
        assert db.queue_snapshot(qid)["pending_total"] == 14
        assert client.post("/api/history/queues/99999/replay",headers=hdr).status_code == 409
        counts = {x["key"]:x["count"] for x in client.get("/api/smart-playlists",headers=hdr).json()["results"]}
        assert counts["top"] == 1 and counts["unheard"] == 0
        top = client.get("/api/smart-playlists/top",headers=hdr).json()
        assert [t["id"] for t in top["tracks"]] == [tracks[0].id]
        assert "file_id" not in str(top)
        db.set_rating(tracks[2].id, 10)
        assert client.get("/api/smart-playlists/top",headers=hdr).json()["total"] == 2
        played = client.post("/api/smart-playlists/top/play",headers=hdr)
        assert played.status_code == 200 and played.json()["sent"] == 2
        assert [t.id for t in db.delivered_queue_tracks(played.json()["queue_id"])] == [tracks[2].id,tracks[0].id]
        assert client.post("/api/smart-playlists/unheard/play",headers=hdr).status_code == 409
        assert client.get("/api/smart-playlists/unknown",headers=hdr).status_code == 404
        assert db.playback_stats() == {}
