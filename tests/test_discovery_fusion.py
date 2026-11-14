"""Offline multi-seed ranking tests; no remote accounts or audio required."""
import asyncio

import pytest

from music_bot.catalog import CatalogTrack
from music_bot.discovery_ranking import fuse_artist_radio
from music_bot.personalized import personalized_discovery, ranked_seed_artists, strongly_negative_artists
from music_bot.storage import Library

A = "8a49dba0-253a-4535-b87f-78bb035336ce"
B = "f3bba4cd-8018-468b-902e-bc8f029593e5"
C = "8e74dd9d-e5a3-4acd-918a-c36a0f8cda84"
D = "8f0cb94a-eeb4-4cc4-a168-c61687fc0a2e"
E = "7af0d8fa-d87c-4893-8edb-3b2c7b41fd6a"


def test_rank_fusion_prefers_cross_seed_agreement():
    only_one = CatalogTrack(A, "Hit", "One")
    agreed = CatalogTrack(B, "Bridge", "Other")
    another = CatalogTrack(C, "Deep", "Third")
    result = fuse_artist_radio(
        [[only_one, agreed], [another, agreed]], limit=3
    )
    assert [t.mbid for t in result][0] == B
    assert {t.mbid for t in result} == {A, B, C}


def test_fusion_deduplicates_versions_and_is_deterministic():
    first = CatalogTrack(A, "Same Song", "Same Artist")
    duplicate = CatalogTrack(B, "Same Song", "Same Artist")
    alternative = CatalogTrack(C, "Different", "Another Artist")
    pools = [[first, alternative], [duplicate, alternative]]
    result = fuse_artist_radio(pools)
    assert len(result) == 2
    # The same recording identity ranks first in both lists, so it leads.
    assert [t.mbid for t in result] == [A, C]
    assert [t.mbid for t in result] == [
        t.mbid for t in fuse_artist_radio(pools)
    ]
    with pytest.raises(ValueError):
        fuse_artist_radio(pools, k=0)


def test_repeated_rejections_suppress_artist_but_not_when_favorite(tmp_path):
    db = Library(tmp_path / "db.sqlite")
    a = db.add_track("f1", "u1", "Bad #1", "Not My Artist")
    b = db.add_track("f2", "u2", "Bad #2", "Not My Artist")
    db.set_rating(a.id, 1)
    db.dislike(b.id)
    negatives = strongly_negative_artists(
        db.all_tracks(), db.liked_ids(), db.disliked_ids(),
        db.ratings(), db.playback_stats()
    )
    assert negatives == {"not my artist"}
    c = db.add_track("f3", "u3", "Good One", "Not My Artist")
    db.set_rating(c.id, 9)
    negatives = strongly_negative_artists(
        db.all_tracks(), db.liked_ids(), db.disliked_ids(),
        db.ratings(), db.playback_stats()
    )
    assert negatives == set()


def test_personalized_radio_filters_strong_negative_artists(tmp_path):
    db = Library(tmp_path / "db.sqlite")
    seed = db.add_track("fs", "us", "Loved", "Seed")
    db.set_rating(seed.id, 10)
    for i in range(2):
        disliked = db.add_track(f"fb{i}", f"ub{i}", f"Bad {i}", "Bad Band")
        db.dislike(disliked.id)

    class FakeCatalog:
        async def artist_radio(self, name, *, difficulty, limit):
            return [
                CatalogTrack(A, "Fresh Song", "Bad Band"),
                CatalogTrack(B, "OK Song", "Good Band"),
            ]

    artists, candidates = asyncio.run(personalized_discovery(
        db, FakeCatalog()
    ))
    assert artists == ["Seed"]
    assert [c.mbid for c in candidates] == [B]


def test_cold_start_uses_saved_artists_without_fake_listens(tmp_path):
    db = Library(tmp_path / "db.sqlite")
    db.add_track("fa1", "ua1", "One", "Very Saved")
    db.add_track("fa2", "ua2", "Two", "Very Saved")
    db.add_track("fb", "ub", "Three", "Other")
    seeds = ranked_seed_artists(db, count=3)
    assert [seed.name for seed in seeds] == ["Very Saved", "Other"]
    assert seeds[0].score < 1.0  # weak inference, not a fabricated rating
    assert db.playback_stats() == {}
    assert db.ratings() == {}
    assert db.liked_ids() == set()


def test_cold_start_ignores_repeatedly_rejected_artist(tmp_path):
    db = Library(tmp_path / "db.sqlite")
    for n in (1, 2):
        t = db.add_track(f"badf{n}", f"badu{n}", f"Bad{n}", "Rejected")
        db.set_rating(t.id, 1)
    db.add_track("good", "gooduid", "OK", "Unrated")
    assert [seed.name for seed in ranked_seed_artists(db)] == ["Unrated"]
