"""Discovery remains owner-only, metadata-only and cannot fake Telegram assets."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from music_bot.api import create_app
from music_bot.catalog import CatalogTrack, CatalogError
from music_bot.config import Settings
from music_bot.storage import Library

TOKEN = "123:offline-test-token"
OWNER = 424242
RECORDING = "8a49dba0-253a-4535-b87f-78bb035336ce"


def telegram_init(user_id=OWNER):
    data = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": user_id}, separators=(",", ":")),
    }
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


class FakeCatalog:
    def __init__(self):
        self.searches = []
        self.radios = []

    async def search(self, text, limit=15, *, allow_variants=True):
        self.searches.append((text, limit, allow_variants))
        return [CatalogTrack(RECORDING, "Roads", "Portishead")]

    async def artist_radio(self, artist, *, difficulty="medium", limit=12):
        self.radios.append((artist, difficulty, limit))
        return [CatalogTrack(RECORDING, "Roads", "Portishead")]


def test_auth_and_metadata_only_wishlist(tmp_path):
    settings = Settings(TOKEN, OWNER, tmp_path / "music.db")
    catalog = FakeCatalog()
    app = create_app(settings, catalog=catalog)
    with TestClient(app) as client:
        assert client.get("/api/discovery/search?q=Roads").status_code == 401
        bad = {"X-Telegram-Init-Data": telegram_init(user_id=999)}
        assert client.get("/api/discovery/search?q=Roads", headers=bad).status_code == 401
        good = {"X-Telegram-Init-Data": telegram_init()}
        missing = client.post(f"/api/discovery/wishlist/{RECORDING}", headers=good)
        assert missing.status_code == 404
        response = client.get("/api/discovery/search?q=Roads", headers=good)
        assert response.status_code == 200
        row = response.json()["results"][0]
        assert row["mbid"] == RECORDING
        assert row["playable"] is False
        assert row["local_track_id"] is None
        assert "file_id" not in response.text
        assert "download_url" not in response.text
        assert catalog.searches == [("Roads", 15, False)]

        add = client.post(f"/api/discovery/wishlist/{RECORDING}", headers=good)
        assert add.status_code == 200 and add.json()["saved"] is True
        second = client.post(f"/api/discovery/wishlist/{RECORDING}", headers=good)
        assert second.json()["saved"] is False
        wishlist = client.get("/api/discovery/wishlist", headers=good).json()
        assert wishlist["results"][0]["playable"] is False
        assert wishlist["results"][0]["title"] == "Roads"

        lib = Library(settings.db_path)
        lib.add_track("real-telegram-file-id", "unique-1", "Roads", "Portishead")
        now_found = client.get("/api/discovery/search?q=Roads", headers=good)
        assert now_found.json()["results"][0]["local_track_id"] is None
        assert now_found.json()["results"][0]["possible_match_track_id"] is not None
        assert now_found.json()["results"][0]["match_confirmed"] is False
        assert now_found.json()["results"][0]["playable"] is False  # metadata never is media
        wishlist_after_upload = client.get("/api/discovery/wishlist", headers=good).json()
        assert wishlist_after_upload["results"][0]["local_track_id"] is None
        assert wishlist_after_upload["results"][0]["possible_match_track_id"] is not None
        assert wishlist_after_upload["results"][0]["playable"] is False

        removed = client.delete(f"/api/discovery/wishlist/{RECORDING}", headers=good)
        assert removed.status_code == 200 and removed.json()["removed"] is True
        assert client.get("/api/discovery/wishlist", headers=good).json()["results"] == []


def test_radio_defaults_to_owner_favorite_artist(tmp_path):
    settings = Settings(TOKEN, OWNER, tmp_path / "music.db")
    catalog = FakeCatalog()
    lib = Library(settings.db_path)
    favorite = lib.add_track("file", "uid", "Roads", "Portishead")
    lib.like(favorite.id)
    with TestClient(create_app(settings, catalog=catalog)) as client:
        hdr = {"X-Telegram-Init-Data": telegram_init()}
        response = client.get("/api/discovery/radio?difficulty=hard", headers=hdr)
        assert response.status_code == 200
        assert response.json()["artist_seed"] == "Portishead"
        assert catalog.radios[-1] == ("Portishead", "hard", 12)
        # An unknown recording cannot be added directly by arbitrary MBID.
        assert client.post(
            "/api/discovery/wishlist/a0000000-0000-0000-0000-000000000000",
            headers=hdr,
        ).status_code == 404


def test_radio_requires_seed_and_external_errors_are_safe(tmp_path):
    settings = Settings(TOKEN, OWNER, tmp_path / "music.db")
    catalog = FakeCatalog()
    with TestClient(create_app(settings, catalog=catalog)) as client:
        hdr = {"X-Telegram-Init-Data": telegram_init()}
        assert client.get("/api/discovery/radio", headers=hdr).status_code == 409
        assert client.get("/api/discovery/search?q=a", headers=hdr).status_code == 422
        assert client.post(
            "/api/discovery/wishlist/evil/path", headers=hdr,
        ).status_code in (404, 422)

        async def unavailable(*args, **kwargs):
            raise CatalogError("Каталог временно недоступен")

        catalog.search = unavailable
        response = client.get("/api/discovery/search?q=Roads", headers=hdr)
        assert response.status_code == 502
        assert response.json()["detail"] == "Каталог временно недоступен"


def test_candidates_wishlist_persists_db_reopen(tmp_path):
    path = tmp_path / "music.db"
    lib = Library(path)
    lib.remember_candidates([CatalogTrack(RECORDING, "Roads", "Portishead")])
    assert lib.wishlist_add(RECORDING)
    again = Library(path)
    assert again.wishlist_ids() == {RECORDING}
    assert again.wishlist(10)[0]["artist"] == "Portishead"
