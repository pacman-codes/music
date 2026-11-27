"""Owner-only album collection shared between the bot and Mini App."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from music_bot.api import create_app
from music_bot.catalog import CatalogError
from music_bot.config import Settings
from music_bot.storage import Library

TOKEN = "1234:test"
OWNER = 42
RELEASE = "8a49dba0-253a-4535-b87f-78bb035336ce"
FIRST = "f3bba4cd-8018-468b-902e-bc8f029593e5"
SECOND = "8e74dd9d-e5a3-4acd-918a-c36a0f8cda84"


def auth(user=OWNER):
    data = {"auth_date": str(int(time.time())),
            "user": json.dumps({"id": user}, separators=(",", ":"))}
    key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    data["hash"] = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(data)}


class Catalog:
    async def _json(self, provider, path, params, **kwargs):
        assert provider == "mb"
        credit = [{"name": "Artist"}]
        release = {"id": RELEASE, "title": "Album", "artist-credit": credit,
                   "release-group": {"id": RELEASE, "primary-type": "Album"},
                   "track-count": 2, "status": "Official"}
        if path == "/ws/2/release/":
            return {"releases": [release]}
        if path == "/ws/2/release/" + RELEASE:
            return {**release, "media": [{"tracks": [
                {"title": "First", "recording": {"id": FIRST}},
                {"title": "Second", "recording": {"id": SECOND}},
            ]}]}
        raise CatalogError("Missing")


def test_album_api_shares_collection_and_does_not_fake_audio(tmp_path):
    cfg = Settings(TOKEN, OWNER, tmp_path / "music.db")
    lib = Library(cfg.db_path)
    with TestClient(create_app(cfg, catalog=Catalog())) as client:
        for path in ("/api/albums", "/api/albums/search?q=Artist+Album",
                     "/api/albums/" + RELEASE):
            assert client.get(path).status_code == 401
            assert client.get(path, headers=auth(999)).status_code == 401
        hdr = auth()
        path = "/api/albums/" + RELEASE
        assert client.post(path).status_code == 401
        assert client.delete(path).status_code == 401
        assert client.post(path + "/play").status_code == 401
        found = client.get("/api/albums/search?q=Artist+Album", headers=hdr)
        assert found.status_code == 200
        assert found.json()["results"][0]["mbid"] == RELEASE
        assert not found.json()["results"][0]["saved"]
        assert found.json()["results"][0]["cover_urls"] == [
            f"https://coverartarchive.org/release/{RELEASE}/front-250",
            f"https://coverartarchive.org/release-group/{RELEASE}/front-250",
        ]
        assert client.post(path, headers=hdr).json()["changed"]
        assert not client.post(path, headers=hdr).json()["changed"]
        assert lib.get_saved_album(RELEASE)["title"] == "Album"
        detail = client.get(path, headers=hdr)
        assert detail.status_code == 200
        row = detail.json()
        assert row["saved"] and not row["playable"]
        assert row["cover_urls"][0] == f"https://coverartarchive.org/release/{RELEASE}/front-250"
        assert [x["title"] for x in row["tracks"]] == ["First", "Second"]
        assert all(x["local_track_id"] is None for x in row["tracks"])
        assert "file_id" not in detail.text
        assert client.post(path + "/play", headers=hdr).status_code == 409
        assert client.post(path + "/play?partial=true", headers=hdr).status_code == 409
        assert row["available_count"] == 0 and row["queue_count"] == 0
        from music_bot.catalog import CatalogTrack
        lib.remember_candidates([CatalogTrack(FIRST, "First", "Artist")])
        first = lib.add_track("file-first", "uid-first", "First", "Artist")
        lib.confirm_recording_match(first.id, FIRST)
        row = client.get(path, headers=hdr).json()
        assert row["available_count"] == 1 and row["queue_count"] == 1
        assert row["tracks"][0]["local_track_id"] == first.id
        assert not row["playable"]
        assert client.post(path + "/play", headers=hdr).status_code == 409
        assert client.get(path + "/tracks/1/youtube-suggestions").status_code == 401

        assert client.delete(path, headers=hdr).json()["removed"]
        assert lib.get_saved_album(RELEASE) is None
        lib.save_album(RELEASE, "Album", "Artist", 2)
        assert client.get("/api/albums", headers=hdr).json()["results"][0]["mbid"] == RELEASE
        assert client.get("/api/albums/invalid", headers=hdr).status_code == 422
        assert client.get("/api/albums?offset=-1", headers=hdr).status_code == 422


def test_album_cover_urls_are_fixed_host_and_cached_group(tmp_path):
    import json
    from music_bot.albums import album_cover_urls, cached_album_group, RELEASE_LOOKUP_PARAMS
    from music_bot.catalog import MusicCatalog
    catalog = MusicCatalog(tmp_path / "cache.db", client=object())
    key = json.dumps(["mb", "/ws/2/release/" + RELEASE,
                      sorted(RELEASE_LOOKUP_PARAMS.items())],
                     sort_keys=True, separators=(",", ":"))
    catalog.cache.put(key, {"release-group": {"id": SECOND}}, 3600)
    assert cached_album_group(catalog, RELEASE) == SECOND
    assert album_cover_urls(RELEASE, SECOND) == [
        f"https://coverartarchive.org/release/{RELEASE}/front-250",
        f"https://coverartarchive.org/release-group/{SECOND}/front-250",
    ]
    assert album_cover_urls(RELEASE, "https://example.com/image.jpg") == [
        f"https://coverartarchive.org/release/{RELEASE}/front-250",
    ]
    assert album_cover_urls("https://example.com/evil") == []
    assert album_cover_urls("it-123") == []


def test_missing_album_suggestions_owner_only_no_import(tmp_path):
    from music_bot.youtube import VideoCandidate
    from music_bot.catalog import CatalogTrack

    class Source:
        def __init__(self):
            self.queries = []
        async def search(self, query, *, limit):
            self.queries.append(query)
            return [VideoCandidate("abcdefghijk", "Artist Second", "Artist", 120)]

    cfg = Settings(TOKEN, OWNER, tmp_path / "music.db", None, (), True, False)
    db = Library(cfg.db_path)
    db.remember_candidates([CatalogTrack(FIRST, "First", "Artist")])
    track = db.add_track("first-file", "first-unique", "First", "Artist")
    db.confirm_recording_match(track.id, FIRST)
    worker = Source()
    with TestClient(create_app(cfg, catalog=Catalog(), youtube_source=worker)) as client:
        path = "/api/albums/" + RELEASE + "/missing-suggestions"
        assert client.post(path).status_code == 401
        assert client.post(path, headers=auth(999)).status_code == 401
        assert client.post(path + "?start=3", headers=auth()).status_code == 422
        result = client.post(path, headers=auth())
        assert result.status_code == 200
        data = result.json()
        assert not data["auto_imported"]
        assert data["next_position"] is None
        assert len(data["results"]) == 1
        entry = data["results"][0]
        assert entry["position"] == 1
        assert entry["suggestions"][0]["recording_mbid"] == SECOND
        assert not entry["suggestions"][0]["match_confirmed"]
        assert worker.queries == ["Artist Second"]
        assert len(db.all_tracks()) == 1
        assert db.get_youtube_video("abcdefghijk")["track_id"] is None

        single = "/api/albums/" + RELEASE + "/tracks/1/youtube-suggestions"
        assert client.get(single, headers=auth(999)).status_code == 401
        assert client.get(single.replace("/tracks/1/", "/tracks/2/"),
                          headers=auth()).status_code == 404
        response = client.get(single, headers=auth())
        assert response.status_code == 200
        assert response.json()["suggestions"][0]["recording_mbid"] == SECOND
        assert response.json()["auto_imported"] is False
        assert len(db.all_tracks()) == 1
        assert worker.queries == ["Artist Second", "Artist Second"]



def test_batch_pagination_and_errors_do_not_block_other_tracks(tmp_path):
    import asyncio
    import uuid
    from music_bot.catalog import CatalogTrack
    from music_bot.telegram_youtube import album_missing_suggestions
    from music_bot.youtube import YouTubeError, VideoCandidate
    db = Library(tmp_path / "music.db")
    tracks = [CatalogTrack(str(uuid.UUID(int=i)), f"Song {i}", "Artist")
              for i in range(1, 6)]

    class Source:
        async def search(self, query, *, limit):
            if query.endswith("2"):
                raise YouTubeError("temporary")
            return [VideoCandidate("abcdefghijk", query, "Artist", 120)]

    result = asyncio.run(album_missing_suggestions(db, Source(), tracks))
    assert [r["position"] for r in result["results"]] == [0, 1, 2]
    assert result["results"][1]["error"]
    assert result["results"][2]["suggestions"]
    assert result["next_position"] == 3 and result["remaining"] == 2
    result = asyncio.run(album_missing_suggestions(db, Source(), tracks, start=3))
    assert [r["position"] for r in result["results"]] == [3, 4]
    assert result["next_position"] is None
    assert not db.all_tracks()


def test_album_library_link_is_explicit_and_shared(tmp_path):
    from music_bot.albums import StoreTrack
    from music_bot.telegram_youtube import album_track_ids, saved_album_ids
    from music_bot.catalog import CatalogTrack

    class AppleCatalog:
        async def _json(self, provider, path, params, **kwargs):
            assert provider == "itunes" and path == "/lookup"
            return {"results": [
                {"wrapperType": "collection", "collectionType": "Album",
                 "collectionId": 100, "collectionName": "Album", "artistName": "Artist",
                 "trackCount": 2},
                *[{"wrapperType": "track", "kind": "song", "collectionId": 100,
                   "trackId": ident, "trackName": name, "artistName": "Artist",
                   "discNumber": 1, "trackNumber": number, "trackTimeMillis": 120000}
                  for number, ident, name in [(1, 101, "First"), (2, 102, "Second")]]
            ]}

    cfg = Settings(TOKEN, OWNER, tmp_path / "music.db")
    lib = Library(cfg.db_path)
    first = lib.add_track("file-first", "uid-first", "First", "Artist")
    second = lib.add_track("file-second", "uid-second", "Second", "Artist")
    # Local title matches alone cannot authorize album playback.
    tracks = [StoreTrack("it-101", "First", "Artist", 120000),
              StoreTrack("it-102", "Second", "Artist", 120000)]
    assert album_track_ids(lib, tracks) == [None, None]
    with TestClient(create_app(cfg, catalog=AppleCatalog())) as client:
        path = "/api/albums/it-100"
        endpoint = path + "/tracks/0/library-link"
        body = {"recording_id": "it-101", "track_id": first.id}
        assert client.post(endpoint, json=body).status_code == 401
        assert client.post(endpoint, json=body, headers=auth(999)).status_code == 401
        hdr = auth()
        assert client.post(endpoint, headers=hdr, json={**body, "recording_id": "it-102"}).status_code == 409
        assert client.post(endpoint, headers=hdr, json={**body, "track_id": 9999}).status_code == 404
        assert client.post(endpoint, headers=hdr, json=body).json()["changed"]
        assert not client.post(endpoint, headers=hdr, json=body).json()["changed"]
        assert client.post(endpoint, headers=hdr, json={**body, "track_id": second.id}).status_code == 409
        assert client.post(path + "/tracks/1/library-link", headers=hdr,
                           json={"recording_id": "it-102", "track_id": second.id}).json()["changed"]
        detail = client.get(path, headers=hdr).json()
        assert detail["playable"] and detail["available_count"] == 2
        assert detail["tracks"][0]["album_linked"]
        assert "file_id" not in detail
        assert saved_album_ids(Library(cfg.db_path), tracks) == [first.id, second.id]
        assert lib.resolved_catalog_tracks() == {}
        assert client.delete(endpoint + "?recording_id=it-101").status_code == 401
        assert client.delete(endpoint + "?recording_id=it-102", headers=hdr).status_code == 409
        assert client.delete(endpoint + "?recording_id=it-101", headers=hdr).json()["removed"]
        assert lib.get_track(first.id) is not None
        assert not client.get(path, headers=hdr).json()["playable"]

    # A verified MusicBrainz link always takes precedence over album-only links.
    mb = CatalogTrack(FIRST, "First", "Artist")
    lib.confirm_album_audio(FIRST, second.id)
    lib.remember_candidates([mb])
    lib.confirm_recording_match(first.id, FIRST)
    assert album_track_ids(lib, [mb]) == [first.id]
    lib.unlink_album_audio(FIRST)
    assert album_track_ids(lib, [mb]) == [first.id]
