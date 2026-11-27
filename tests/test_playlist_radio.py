"""Playlist-specific discovery uses no unrelated global preference seeds."""
import asyncio
import random
import uuid

import pytest

from music_bot.catalog import CatalogError, CatalogTrack
from music_bot.main import playlist_page
from music_bot.personalized import playlist_discovery, playlist_discovery_seeds
from music_bot.storage import Library


def record(n, title, artist, **kwargs):
    return CatalogTrack(str(uuid.UUID(int=n)), title, artist, **kwargs)


def test_playlist_radio_uses_only_playlist_seeds_and_filters_known_tracks(tmp_path):
    db = Library(tmp_path / "music.db")
    pid = db.create_playlist("Night")
    first = db.add_track("f1", "u1", "Known", "Seed One")
    second = db.add_track("f2", "u2", "Other", "Seed Two")
    global_fav = db.add_track("f3", "u3", "Global", "Unrelated")
    rejected = db.add_track("f4", "u4", "Nope", "Rejected")
    db.like(global_fav.id)
    db.set_rating(global_fav.id, 10)
    db.dislike(rejected.id)
    for t in (first, second, rejected):
        db.add_to(pid, t.id)
    seeds = playlist_discovery_seeds(db, pid, rng=random.Random(1))
    assert {s.name for s in seeds} == {"Seed One", "Seed Two"}
    calls = []

    class Catalog:
        async def artist_radio(self, name, *, difficulty, limit):
            calls.append((name, difficulty))
            return [record(1, "Known", "Seed One"),
                    record(2, "New", "New Artist"),
                    record(3, "Remix", "Another", variant=True),
                    record(4, "Global", "Unrelated")]

    used, rows = asyncio.run(playlist_discovery(db, Catalog(), pid, difficulty="hard"))
    assert set(used) == {"Seed One", "Seed Two"}
    assert set(calls) == {("Seed One", "hard"), ("Seed Two", "hard")}
    assert [r.title for r in rows] == ["New"]
    _, rows = asyncio.run(playlist_discovery(db, Catalog(), pid))
    assert rows == []
    assert db.playback_stats() == {}
    assert len(db.all_tracks()) == 4
    _, markup = playlist_page(db, pid)
    assert any(b.callback_data == f"plradio:{pid}:medium"
               for row in markup.inline_keyboard for b in row)


def test_playlist_radio_empty_and_partial_outage(tmp_path):
    db = Library(tmp_path / "music.db")
    pid = db.create_playlist("Empty")

    class Catalog:
        async def artist_radio(self, name, **kwargs):
            if name == "Fail":
                raise CatalogError("Unavailable")
            return [record(5, "Fresh", "Another Artist")]

    assert asyncio.run(playlist_discovery(db, Catalog(), pid)) == ([], [])
    with pytest.raises(ValueError):
        asyncio.run(playlist_discovery(db, Catalog(), 9999))
    for i, artist in enumerate(("Fail", "Works")):
        track = db.add_track(f"f{i}", f"u{i}", "Title", artist)
        db.add_to(pid, track.id)
    used, rows = asyncio.run(playlist_discovery(db, Catalog(), pid))
    assert used == ["Works"]
    assert [row.title for row in rows] == ["Fresh"]
