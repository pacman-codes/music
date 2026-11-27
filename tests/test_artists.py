"""Artist discovery and library views keep identity and audio separate."""
import asyncio
import uuid

import pytest

from music_bot.artists import Artist, artist_discography, group_album, search_artists
from music_bot.catalog import CatalogError, CatalogTrack
from music_bot.main import create_router, home_keyboard
from music_bot.storage import Library
from music_bot.telegram_artists import artist_home, artist_page


A, B, G, R, T = [str(uuid.UUID(int=i)) for i in range(1,6)]


def test_artist_favorites_and_library_identity(tmp_path):
    db = Library(tmp_path / "music.db")
    db.remember_artists([Artist(A, "Artist", "First"), Artist(B, "Artist", "Second")])
    assert db.set_artist_saved(A, True)
    assert not db.set_artist_saved(A, True)
    local = db.add_track("f1", "u1", "Local", "ARTIST")
    other = db.add_track("f2", "u2", "Other", "Artist")
    unrelated = db.add_track("f3", "u3", "Other name", "Artist Extra")
    db.remember_candidates([CatalogTrack(T, "Other", "Artist", B)])
    db.confirm_recording_match(other.id, T)
    assert [t.id for t in db.artist_library_tracks(A, "Artist")] == [local.id]
    assert {t.id for t in db.artist_library_tracks(B, "Artist")} == {local.id, other.id}
    text, kb = artist_page(db, A)
    assert "Local" in text and "Other name" not in text
    assert any(b.callback_data == f"artistdisc:{A}:0" for row in kb.inline_keyboard for b in row)
    reopened = Library(db.path)
    assert [r["mbid"] for r in reopened.saved_artists()] == [A]
    db.remember_artists([Artist(A, "Artist", "Updated")])
    assert db.get_artist(A)["saved"] == 1
    assert db.set_artist_saved(A, False)
    assert not db.saved_artists()
    assert db.get_track(local.id) is not None
    assert db.get_track(unrelated.id) is not None
    assert any(b.callback_data == "artistlist:0" for row in home_keyboard().inline_keyboard for b in row)
    assert any(b.callback_data == "artistsearch" for row in artist_home(db)[1].inline_keyboard for b in row)
    assert create_router(db, 42)


def test_artist_catalog_identity_and_discography():
    class Catalog:
        async def _json(self, provider, path, params, **kwargs):
            assert provider == "mb"
            if path == "/ws/2/artist/":
                return {"artists": [{"id": B, "name": "Artist Extra"},
                                    {"id": A, "name": "Artist", "disambiguation": "Band"}]}
            if path == "/ws/2/release-group/":
                assert A in params["query"]
                credit = [{"artist": {"id": A, "name": "Artist"}, "name": "Artist"}]
                base = {"id": G, "title": "Album", "primary-type": "Album",
                        "first-release-date": "2020", "artist-credit": credit}
                return {"count": 11, "release-groups": [base, base,
                        {**base, "id": B, "secondary-types": ["Live"]},
                        {**base, "id": T, "artist-credit": [{"artist": {"id": B}}]}]}
            if path == "/ws/2/release/":
                assert G in params["query"]
                return {"releases": [{"id": R, "title": "Album", "status": "Official",
                         "date": "2020", "track-count": 8,
                         "artist-credit": [{"name": "Artist", "artist": {"id": A}}],
                         "release-group": {"id": G, "primary-type": "Album"}}]}
            raise AssertionError(path)

    async def scenario():
        found = await search_artists(Catalog(), "Artist")
        assert [a.mbid for a in found] == [A, B]
        rows, more = await artist_discography(Catalog(), A)
        assert [r.mbid for r in rows] == [G] and more
        album = await group_album(Catalog(), G)
        assert album.mbid == R
        with pytest.raises(ValueError):
            await artist_discography(Catalog(), "invalid")
        with pytest.raises(ValueError):
            await group_album(Catalog(), "invalid")
    asyncio.run(scenario())
