"""Offline contract tests for MusicBrainz and ListenBrainz discovery."""
import asyncio
from pathlib import Path

import httpx
import pytest

from music_bot.catalog import (
    CatalogCache, CatalogError, MusicCatalog, is_variant,
    normalized_identity, parse_recording, unique_tracks,
)

A = "8a49dba0-253a-4535-b87f-78bb035336ce"
B = "f3bba4cd-8018-468b-902e-bc8f029593e5"
C = "8e74dd9d-e5a3-4acd-918a-c36a0f8cda84"
ARTIST = "8f6bd1e4-fbe1-4f50-aa9b-94c450ec0f11"
OTHER = "5adcb9d9-5ea2-428d-af46-ef626966e106"


def recording(id_, title, artist="Portishead", artist_id=ARTIST):
    return {
        "id": id_, "title": title, "length": 180000,
        "artist-credit": [{"name": artist,
                           "artist": {"name": artist, "id": artist_id}}],
    }


def test_parse_and_dedup_versions():
    first = parse_recording(recording(A, "Roads"))
    duplicate = parse_recording(recording(B, "Roads"))
    remix = parse_recording(recording(C, "Roads (Live)"))
    assert first is not None and first.artist == "Portishead"
    assert first.duration_ms == 180000
    assert remix.variant and is_variant("Song - nightcore")
    assert normalized_identity("  ROADS ", "  portishead") == (
        "roads", "portishead",
    )
    assert [t.mbid for t in unique_tracks([first, duplicate, remix], 20,
                                           allow_variants=False)] == [A]
    assert [t.mbid for t in unique_tracks([first, duplicate, remix], 20,
                                           allow_variants=True)] == [A, C]
    assert parse_recording({"id": "invalid", "title": "Bad"}) is None


def test_search_uses_correct_upstream_and_sqlite_cache(tmp_path):
    calls = []

    def handle(request: httpx.Request):
        calls.append(request)
        assert request.url.host == "musicbrainz.org"
        assert request.url.path == "/ws/2/recording/"
        assert request.headers["user-agent"].startswith("music-personal-bot/")
        assert request.url.params.get("fmt") == "json"
        return httpx.Response(200, json={"recordings": [
            recording(A, "Roads"),
            recording(B, "Roads"),
            recording(C, "Roads (Live)"),
        ]})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            catalog = MusicCatalog(tmp_path / "music.db", http)
            first = await catalog.search("Portishead Roads", allow_variants=False)
            second = await catalog.search("Portishead Roads", allow_variants=False)
            assert [t.mbid for t in first] == [A]
            assert first == second
            assert len(calls) == 1
        # New process instance should hit durable cache without a network call.
        def forbidden(request):
            raise AssertionError("cache missed across catalog instances")
        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
            catalog = MusicCatalog(tmp_path / "music.db", http)
            rows = await catalog.search("Portishead Roads", allow_variants=False)
            assert rows == first

    asyncio.run(scenario())


def test_listenbrainz_artist_radio_resolves_musicbrainz_metadata(tmp_path):
    calls = []

    def handle(request: httpx.Request):
        calls.append(request)
        if request.url.host == "musicbrainz.org" and "/artist/" in request.url.path:
            return httpx.Response(200, json={"artists": [
                {"id": OTHER, "name": "Portishead-ish"},
                {"id": ARTIST, "name": "Portishead"},
            ]})
        if request.url.host == "api.listenbrainz.org":
            assert ARTIST in request.url.path
            assert request.url.params["mode"] == "medium"
            assert request.url.params["pop_begin"] == "0"
            assert request.url.params["pop_end"] == "100"
            return httpx.Response(200, json={
                ARTIST: [
                    {"recording_mbid": A, "total_listen_count": 123},
                    {"recording_mbid": C, "total_listen_count": 80},
                ],
                OTHER: [
                    {"recording_mbid": B, "total_listen_count": 99},
                ],
            })
        if request.url.host == "musicbrainz.org" and "/recording/" in request.url.path:
            query = request.url.params["query"]
            assert f"rid:{A}" in query and f"rid:{B}" in query
            # Intentionally shuffled: output must preserve LB collaborative order.
            return httpx.Response(200, json={"recordings": [
                recording(B, "Teardrop", "Massive Attack", OTHER),
                recording(C, "Angel (Live)", "Massive Attack", OTHER),
                recording(A, "Roads"),
            ]})
        raise AssertionError(f"unexpected request: {request.url}")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            catalog = MusicCatalog(tmp_path / "cache.db", http)
            rows = await catalog.artist_radio("Portishead", difficulty="medium", limit=5)
            assert [r.mbid for r in rows] == [A, B]
            assert [r.title for r in rows] == ["Roads", "Teardrop"]
            # C is a live variant and should never displace the original.
            assert len(calls) == 3
            repeated = await catalog.artist_radio("Portishead", difficulty="medium", limit=5)
            assert repeated == rows and len(calls) == 3

    asyncio.run(scenario())


def test_bad_upstream_redirect_and_oversized_response_rejected(tmp_path):
    scenarios = [
        lambda request: httpx.Response(302, headers={"location": "https://example.com/evil"}),
        lambda request: httpx.Response(
            200, headers={"content-type": "application/json",
                          "content-length": "4000000"},
            content=b"{}",
        ),
        lambda request: httpx.Response(
            200, headers={"content-type": "text/html"}, text="<html>no</html>"
        ),
    ]
    for case in scenarios:
        async def scenario():
            async with httpx.AsyncClient(transport=httpx.MockTransport(case)) as http:
                catalog = MusicCatalog(tmp_path / "failed.db", http)
                with pytest.raises(CatalogError):
                    await catalog.search("Artist Song")
        asyncio.run(scenario())


def test_search_rejects_unbounded_user_inputs_without_network(tmp_path):
    def forbidden(request):
        raise AssertionError("Invalid input must not make network requests")

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
            catalog = MusicCatalog(tmp_path / "safe.db", http)
            with pytest.raises(ValueError):
                await catalog.search("x")
            with pytest.raises(ValueError):
                await catalog.search("x" * 101)
            with pytest.raises(ValueError):
                await catalog.search("valid", limit=1000000)
            with pytest.raises(ValueError):
                await catalog.artist_radio("Portishead", difficulty="surprise")
    asyncio.run(scenario())


def test_cache_respects_expiry(tmp_path):
    cache = CatalogCache(tmp_path / "cache.sqlite")
    cache.put("foo", {"value": 1}, 10, now=100)
    assert cache.get("foo", now=109) == {"value": 1}
    assert cache.get("foo", now=110) is None
    with pytest.raises(ValueError):
        cache.put("bar", {}, 1000000000)
