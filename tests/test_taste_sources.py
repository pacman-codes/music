"""Public music sources are metadata-only and fail closed on partial exports."""
import asyncio
import json

import httpx
import pytest

from music_bot.storage import Library
from music_bot.profile_export import export_profile
from music_bot.personalized import ranked_seed_artists
from music_bot.taste_sources import (
    SourceSong, TasteSourceError, parse_yandex_url, parse_yandex_playlist,
    fetch_yandex_playlist, parse_youtube_csv, parse_youtube_export,
    parse_youtube_page_text,
)

YM_URL = "https://music.yandex.ru/playlists/lk.aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def yandex_payload(n=2, count=None):
    return {"result": {
        "trackCount": count if count is not None else n,
        "tracks": [
            {"id": i+1, "timestamp": "2026-11-10T12:00:00+00:00",
             "track": {"id": i+1, "title": "Song " + str(i+1),
                       "artists": [{"name": "First Artist" if i == 0 else "Second Artist"}],
                       "albums": [{"id": 77}]}}
            for i in range(n)
        ],
    }}


def test_yandex_only_fixed_https_host_and_uuid():
    assert parse_yandex_url(YM_URL + "?utm_source=sharing").startswith("lk.")
    for url in (
        "http://music.yandex.ru/playlists/lk.aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "https://evil.example/playlists/lk.aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "https://music.yandex.ru.evil.example/playlists/lk.aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "https://music.yandex.ru/track/123",
        "https://music.yandex.ru/playlists/../../private",
        "https://music.yandex.ru:555/playlists/lk.aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "https://user:pass@music.yandex.ru/playlists/lk.aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    ):
        with pytest.raises(TasteSourceError):
            parse_yandex_url(url)


def test_yandex_playlist_records_artist_dates_and_exact_track_links():
    songs = parse_yandex_playlist(yandex_payload())
    assert len(songs) == 2
    assert songs[0].artist == "First Artist"
    assert songs[0].title == "Song 1"
    assert songs[0].liked_at.startswith("2026-11-10")
    assert songs[0].url == "https://music.yandex.ru/album/77/track/1"
    assert len({s.external_id for s in songs}) == 2
    with pytest.raises(TasteSourceError):
        parse_yandex_playlist(yandex_payload(n=1, count=100))


def test_yandex_fetch_requests_only_fixed_host_and_never_redirects():
    requested = []
    def handler(request):
        requested.append(str(request.url))
        return httpx.Response(200, json=yandex_payload())
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_yandex_playlist(YM_URL, client=client)
    songs = asyncio.run(scenario())
    assert len(songs) == 2
    assert requested == [
        "https://api.music.yandex.ru/playlist/lk.aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    ]


def test_youtube_takeout_library_csv_preserves_video_ids_and_tracks():
    csv_bytes = (
        "Video ID,Song Title,Album Title,Artist Name\n"
        "abcdefghijk,Secret Theme,Album A,Experimental Band\n"
        "zyxwvutsrqp,Second,Album B,Other Band\n"
    ).encode()
    songs, complete = parse_youtube_export(csv_bytes, "music-library-songs.csv")
    assert not complete  # Not necessarily a full liked-video list
    assert songs[0].external_id == "abcdefghijk"
    assert songs[0].artist == "Experimental Band"
    assert songs[0].url == "https://music.youtube.com/watch?v=abcdefghijk"
    assert len(songs) == 2


def test_youtube_playlist_csv_with_ids_only_is_not_misrepresented_as_music():
    with pytest.raises(TasteSourceError, match="ID видео"):
        parse_youtube_csv(
            "Video ID,Playlist Video Creation Timestamp\n"
            "abcdefghijk,2026-11-10T00:00:00+00:00\n"
        )


def test_youtube_copied_page_multi_artist_and_search_links():
    raw = (
        "YouTube Music\nПонравившаяся музыка\n227 треков\n"
        "Example Song\nArtist One\nAlbum A\n3:33\n"
        "Second Song\nArtist Two\nи\nGuest Artist\nAnother Album\n4:14\n"
    )
    songs, complete = parse_youtube_export(raw.encode(), "copied-page.txt")
    assert not complete
    assert len(songs) == 2
    assert songs[0].title == "Example Song"
    assert songs[1].artist == "Artist Two, Guest Artist"
    assert songs[0].url.startswith("https://music.youtube.com/search?")
    assert all(song.external_id.startswith("title-") for song in songs)


def test_source_sync_is_atomic_idempotent_and_youtube_partial_is_additive(tmp_path):
    db = Library(tmp_path / "music.sqlite3")
    a = SourceSong("v1", "Pulse", "Band A", "https://music.youtube.com/watch?v=abcdefghijk")
    b = SourceSong("v2", "Motion", "Band B", "https://music.youtube.com/watch?v=zyxwvutsrqp")
    first = db.sync_taste_source("youtube_music", [a, b], complete=False)
    assert first["active"] == 2 and first["added"] == 2
    second = db.sync_taste_source("youtube_music", [a], complete=False)
    assert second["active"] == 2 and second["added"] == 0
    assert len(db.taste_tracks("youtube_music")) == 2
    with pytest.raises(ValueError):
        db.sync_taste_source("youtube_music", [], complete=True)
    assert db.taste_sources()[0]["count"] == 2
    y1 = SourceSong("1", "First", "Sample", "https://music.yandex.ru/track/1")
    y2 = SourceSong("2", "Second", "Sample", "https://music.yandex.ru/track/2")
    db.sync_taste_source("yandex", [y1, y2], complete=True, public_url=YM_URL)
    changed = db.sync_taste_source("yandex", [y2], complete=True, public_url=YM_URL)
    assert changed["removed"] == 1 and changed["active"] == 1
    assert len(db.taste_tracks("yandex")) == 1
    assert len(db.taste_sources()) == 2


def test_imported_likes_feed_discovery_but_not_local_playback(tmp_path):
    db = Library(tmp_path / "music.db")
    liked = SourceSong("42", "My Special Song", "Obscure Artist",
                       "https://music.yandex.ru/track/42",
                       "2026-11-10T12:00:00Z")
    db.sync_taste_source("yandex", [liked], complete=True, public_url=YM_URL)
    seeds = ranked_seed_artists(db)
    assert seeds[0].name == "Obscure Artist" and seeds[0].score > 0
    assert ("my special song", "obscure artist") in db.taste_identities()
    assert db.liked_ids() == set() and db.all_tracks() == []
    assert db.verdict_counts() == {k:0 for k in db.VERDICT_STATUSES}


def test_external_profile_exports_all_metadata_not_only_first_page(tmp_path):
    db = Library(tmp_path / "music.db")
    songs = [
        SourceSong(str(i), "Song " + str(i), "Test Artist",
                   "https://music.yandex.ru/track/" + str(i))
        for i in range(130)
    ]
    db.sync_taste_source("yandex", songs, complete=True, public_url=YM_URL)
    snapshot = export_profile(db)
    assert len(snapshot["external_liked_tracks"]["yandex"]) == 130
    assert snapshot["external_taste_sources"][0]["count"] == 130
    assert "file_id" not in json.dumps(snapshot["external_liked_tracks"])
