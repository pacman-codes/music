"""Mini App artist profiles share persisted catalog identities with Telegram."""
from fastapi.testclient import TestClient

import music_bot.api as api
from music_bot.artists import Artist
from music_bot.catalog import CatalogTrack, CatalogError
from music_bot.config import Settings
from music_bot.storage import Library
from test_albums_api import auth, TOKEN, OWNER
from test_webapp_queue import FakeTelegramBot

ARTIST = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-000000000002"
GROUP = "00000000-0000-0000-0000-000000000003"
RELEASE = "00000000-0000-0000-0000-000000000004"
RECORDING = "00000000-0000-0000-0000-000000000005"


class Catalog:
    offline = False

    async def _json(self, provider, path, params, **kwargs):
        if self.offline:
            raise CatalogError("Offline")
        assert provider == "mb"
        credit = [{"artist": {"id": ARTIST, "name": "Artist"}, "name": "Artist"}]
        if path == "/ws/2/artist/":
            return {"artists": [{"id": ARTIST, "name": "Artist", "disambiguation": "Band"}]}
        if path == "/ws/2/release-group/":
            return {"count": 1, "release-groups": [{"id": GROUP, "title": "Album",
                    "primary-type": "Album", "artist-credit": credit, "first-release-date": "2021"}]}
        if path == "/ws/2/release/":
            return {"releases": [{"id": RELEASE, "title": "Album", "status": "Official",
                    "artist-credit": credit, "track-count": 8, "date": "2021",
                    "release-group": {"id": GROUP, "primary-type": "Album"}}]}
        raise AssertionError(path)

    async def artist_radio_by_id(self, ident, **kwargs):
        assert ident == ARTIST
        return [CatalogTrack(RECORDING, "Fresh", "Another Artist")]


def test_artist_api_shared_favorites_and_discography(tmp_path, monkeypatch):
    cfg = Settings(TOKEN, OWNER, tmp_path / "music.db")
    lib = Library(cfg.db_path)
    local = lib.add_track("f", "u", "Saved song", "Artist")
    lib.add_track("other", "other-u", "Other song", "Other artist")
    catalog = Catalog()
    class FakeBot(FakeTelegramBot):
        def __init__(self, token):
            self.groups, self.messages, self.single = [], [], []
    bot = FakeBot(TOKEN)
    monkeypatch.setattr(api, "Bot", lambda token: bot)
    path = "/api/artists/" + ARTIST
    with TestClient(api.create_app(cfg, catalog=catalog)) as client:
        for method, url, payload in [
            ("GET", "/api/artists", None), ("GET", "/api/artists/search?q=Artist", None),
            ("GET", path, None), ("PUT", path, {"saved": True}),
            ("GET", path+"/discography", None), ("POST", path+"/play", None),
            ("POST", path+"/radio", None), ("GET", "/api/artist-release-groups/"+GROUP, None)
        ]:
            assert client.request(method,url,json=payload).status_code == 401
            assert client.request(method,url,json=payload,headers=auth(999)).status_code == 401
        hdr = auth()
        assert client.get(path,headers=hdr).status_code == 404
        assert client.get("/api/artists/invalid",headers=hdr).status_code == 422
        found = client.get("/api/artists/search?q=Artist",headers=hdr).json()["results"]
        assert found[0]["mbid"] == ARTIST and not found[0]["saved"]
        assert client.put(path,headers=hdr,json={"saved":True}).json()["changed"]
        assert lib.saved_artists()[0]["mbid"] == ARTIST
        assert client.get("/api/artists",headers=hdr).json()["results"][0]["mbid"] == ARTIST
        detail = client.get(path,headers=hdr).json()
        assert detail["tracks"] == [{"id":local.id,"title":"Saved song","artist":"Artist"}]
        assert "file_id" not in str(detail)
        releases = client.get(path+"/discography",headers=hdr).json()
        assert releases["results"][0]["cover_urls"] == [f"https://coverartarchive.org/release-group/{GROUP}/front-250"]
        assert client.get("/api/artist-release-groups/"+GROUP,headers=hdr).json()["release_id"] == RELEASE
        played = client.post(path+"/play",headers=hdr)
        assert played.status_code == 200 and played.json()["sent"] == 1
        radio = client.post(path+"/radio",headers=hdr).json()["results"]
        assert radio[0]["mbid"] == RECORDING and not radio[0]["playable"]
        assert client.post(path+"/radio",headers=hdr).json()["results"] == []
        catalog.offline = True
        assert client.get(path,headers=hdr).status_code == 200
        assert client.get(path+"/discography",headers=hdr).status_code == 502
        assert client.put(path,headers=hdr,json={"saved":False}).json()["changed"]
        assert not lib.saved_artists()
        assert lib.get_track(local.id) is not None
