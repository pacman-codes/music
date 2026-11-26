"""Live-feedback-based personal discovery across multiple LB radio seeds."""
import asyncio
import random

import pytest

from music_bot.catalog import CatalogError, CatalogTrack
from music_bot.personalized import choose_discovery_seeds, personalized_discovery, ranked_seed_artists
from music_bot.storage import Library

A="8a49dba0-253a-4535-b87f-78bb035336ce"
B="f3bba4cd-8018-468b-902e-bc8f029593e5"
C="8e74dd9d-e5a3-4acd-918a-c36a0f8cda84"
D="8f0cb94a-eeb4-4cc4-a168-c61687fc0a2e"


class FakeRadio:
    def __init__(self, failures=()):
        self.calls=[]
        self.failures=set(failures)

    async def artist_radio(self, artist, *, difficulty, limit):
        self.calls.append((artist,difficulty,limit))
        if artist in self.failures:
            raise CatalogError("upstream unavailable")
        if artist=="Portishead":
            return [CatalogTrack(A, "Roads", "Portishead"),
                    CatalogTrack(B, "Glory Box", "Portishead"),
                    CatalogTrack(C, "Angel", "Massive Attack")]
        if artist=="Massive Attack":
            return [CatalogTrack(C, "Angel", "Massive Attack"),
                    CatalogTrack(D, "Teardrop", "Massive Attack"),
                    CatalogTrack(A, "Roads", "Portishead")]
        return []


@pytest.fixture
def db(tmp_path):
    return Library(tmp_path/"music.db")


def add(db, name, artist, *, score=None, liked=False):
    t=db.add_track("file-"+name,"uid-"+name,name,artist)
    if score is not None: db.set_rating(t.id,score)
    if liked: db.like(t.id)
    return t


def test_low_rating_and_manual_skips_remove_seed_without_unliking(db):
    a=add(db,"One","Portishead",score=10,liked=True)
    b=add(db,"Two","Massive Attack",score=8,liked=True)
    c=add(db,"Three","PoorArtist",score=2,liked=True)
    names=[s.name for s in ranked_seed_artists(db)]
    assert names==["Portishead","Massive Attack"]
    assert c.id in db.liked_ids()
    for _ in range(3): db.record_playback_event(a.id,"skip")
    names=[s.name for s in ranked_seed_artists(db)]
    assert names==["Massive Attack"]
    assert a.id in db.liked_ids()


def test_multiple_artist_radio_round_robin_and_no_duplicate_tracks(db):
    own=add(db,"My saved song","Portishead",score=10,liked=True)
    other=add(db,"Another saved","Massive Attack",score=9,liked=True)
    catalog=FakeRadio()
    seeds, results=asyncio.run(personalized_discovery(db,catalog,limit=10))
    assert seeds==["Portishead","Massive Attack"]
    assert [r.mbid for r in results]==[A,C,B,D]
    assert len(catalog.calls)==2
    assert all(not hasattr(r,"file_id") for r in results)


def test_owned_and_variant_records_excluded(db):
    own=add(db,"Roads","Portishead",score=9,liked=True)
    db.remember_candidates([CatalogTrack(A, "Roads", "Portishead")])
    db.confirm_recording_match(own.id,A)
    second=add(db,"Another","Massive Attack",score=8,liked=True)

    class WithVariants(FakeRadio):
        async def artist_radio(self, artist, *, difficulty, limit):
            return [CatalogTrack(A,"Roads","Portishead"),
                    CatalogTrack(B,"Glory Box (Live)","Portishead",variant=True),
                    CatalogTrack(C,"Angel","Massive Attack")]
    seeds,results=asyncio.run(personalized_discovery(db,WithVariants()))
    assert [r.mbid for r in results]==[C]


def test_partial_upstream_errors_are_tolerated(db):
    add(db,"One","Portishead",score=9,liked=True)
    add(db,"Two","Massive Attack",score=8,liked=True)
    catalog=FakeRadio(failures={"Portishead"})
    seeds,results=asyncio.run(personalized_discovery(db,catalog,limit=5))
    assert seeds==["Massive Attack"]
    assert results

    catalog=FakeRadio(failures={"Portishead","Massive Attack"})
    with pytest.raises(CatalogError):
        asyncio.run(personalized_discovery(db,catalog,limit=5))


def test_empty_profile_and_arguments(db):
    catalog=FakeRadio()
    assert asyncio.run(personalized_discovery(db,catalog)) == ([], [])
    assert catalog.calls==[]
    with pytest.raises(ValueError):
        asyncio.run(personalized_discovery(db,catalog,difficulty="bad"))
    with pytest.raises(ValueError):
        asyncio.run(personalized_discovery(db,catalog,limit=200))


def test_discovery_rotates_across_large_taste_profile(db):
    from music_bot.taste_sources import SourceSong
    songs = [SourceSong(str(i), "Song " + str(i), "Artist " + str(i),
                        "https://music.yandex.ru/track/" + str(i))
             for i in range(1, 21)]
    db.sync_taste_source("yandex", songs, complete=False)
    rng = random.Random(42)
    seen = set()
    for _ in range(30):
        seeds = choose_discovery_seeds(db, rng=rng)
        assert len({seed.name for seed in seeds}) == 3
        seen.update(seed.name for seed in seeds)
    assert len(seen) > 10
    assert db.playback_stats() == {}
    assert db.ratings() == {}


def test_discovery_favorite_is_weighted_but_not_mandatory(db):
    add(db, "Anchor", "Favorite", score=10, liked=True)
    for i in range(5):
        add(db, "Other" + str(i), "Artist " + str(i), score=7)
    rng = random.Random(7)
    batches = [choose_discovery_seeds(db, rng=rng) for _ in range(100)]
    assert all(len({seed.name for seed in seeds}) == 3 for seeds in batches)
    assert any("Favorite" not in {seed.name for seed in seeds} for seeds in batches)
    first = [seeds[0].name for seeds in batches]
    assert first.count("Favorite") > first.count("Artist 0")


def test_repeated_discovery_uses_fresh_candidates(db):
    add(db, "Seed", "Portishead", score=10, liked=True)
    catalog = FakeRadio()
    _, first = asyncio.run(personalized_discovery(db, catalog, limit=2))
    _, second = asyncio.run(personalized_discovery(db, catalog, limit=2))
    _, third = asyncio.run(personalized_discovery(db, catalog, limit=2))
    assert len(first) == 2
    assert len(second) == 1
    assert not ({song.mbid for song in first} & {song.mbid for song in second})
    assert third == []
    assert db.playback_stats() == {}
    assert db.ratings()  # only the original explicit rating


def test_suggestion_history_shared_and_expires(db):
    song = CatalogTrack(A, "Roads", "Portishead")
    alias = CatalogTrack(B, "ROADS", "PORTISHEAD")
    assert db.reserve_discovery_suggestions([song], 1, now=100000) == [song]
    other = Library(db.path)
    assert other.reserve_discovery_suggestions([alias], 1, now=100001) == []
    assert other.recent_discovery_identities(now=100001) == {("roads", "portishead")}
    assert other.reserve_discovery_suggestions([alias], 1, now=186400) == [alias]
