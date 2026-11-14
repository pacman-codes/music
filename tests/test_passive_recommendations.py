"""Zero-click recommendation signals: deliveries are not confirmed listens."""
from collections import Counter

from music_bot.recommend import track_weight
from music_bot.selection import select_wave_batch
from music_bot.storage import Library
from music_bot.wave_v2 import classify, wave_v2


def make_track(db, n, artist):
    return db.add_track(f"file{n}", f"unique{n}", f"Song {n}", artist)


def test_passive_signals_use_only_delivery_history_and_custom_playlists(tmp_path):
    db = Library(tmp_path / "db.sqlite")
    favorite = make_track(db, 1, "Favorite Artist")
    related = make_track(db, 2, "Other Artist")
    unrelated = make_track(db, 3, "Unrelated")
    db.like(favorite.id)
    playlist = db.create_playlist("Road trip")
    db.add_to(playlist, favorite.id)
    db.add_to(playlist, related.id)
    db.record_single_send(favorite.id)
    db.record_single_send(related.id)
    db.record_single_send(favorite.id)

    signals = db.recommendation_signals(db.liked_ids())
    assert signals["related_ids"] == {related.id}
    assert signals["playlist_counts"] == {favorite.id: 1, related.id: 1}
    assert signals["exposure"][favorite.id] > signals["exposure"][related.id] > 0
    assert unrelated.id not in signals["exposure"]
    assert db.playback_stats() == {}
    assert db.ratings() == {}


def test_cross_artist_playlist_changes_wave_classification(tmp_path):
    db = Library(tmp_path / "db.sqlite")
    seed = make_track(db, 1, "Seed Artist")
    candidate = make_track(db, 2, "Different Artist")
    outside = make_track(db, 3, "Third Artist")
    db.like(seed.id)
    playlist = db.create_playlist("Mood")
    db.add_to(playlist, seed.id)
    db.add_to(playlist, candidate.id)
    signals = db.recommendation_signals(db.liked_ids())
    groups = classify(db.all_tracks(), db.liked_ids(), db.ratings(),
                      {}, signals["related_ids"])
    assert {x.id for x in groups["familiar"]} == {seed.id}
    assert {x.id for x in groups["similar"]} == {candidate.id}
    assert {x.id for x in groups["new"]} == {outside.id}
    batch = select_wave_batch(db, "balanced", count=3)
    assert {t.id for t in batch} == {seed.id, candidate.id, outside.id}


def test_ranking_penalizes_exposure_without_implying_dislike(tmp_path):
    db = Library(tmp_path / "db.sqlite")
    a = make_track(db, 1, "Band")
    kwargs = dict(liked_ids={a.id}, favorite_artists=Counter(),
                  seen_artist=Counter(), ratings={a.id: 9})
    base = track_weight(a, **kwargs)
    recent = track_weight(a, **kwargs, recent_ids={a.id}, exposure={a.id: 2.0})
    playlist = track_weight(a, **kwargs, playlist_counts={a.id: 2},
                            related_ids={a.id})
    same_artist = track_weight(a, **kwargs, recent_artists=["other", "band"])
    assert 0 < recent < base
    assert playlist > base
    assert same_artist < base
    assert db.disliked_ids() == set()


def test_wave_preserves_quotas_with_passive_context(tmp_path):
    db = Library(tmp_path / "db.sqlite")
    tracks = []
    for i in range(7):
        t = make_track(db, i, "Loved")
        db.like(t.id)
        tracks.append(t)
    for i in range(7, 14):
        tracks.append(make_track(db, i, "Loved"))
    for i in range(14, 24):
        tracks.append(make_track(db, i, "Other " + str(i % 4)))
    signals = db.recommendation_signals(db.liked_ids())
    result = wave_v2(tracks, db.liked_ids(), ratings=db.ratings(), count=10,
                     preset="balanced", seed=14, **signals)
    again = wave_v2(tracks, db.liked_ids(), ratings=db.ratings(), count=10,
                    preset="balanced", seed=14, **signals)
    assert [t.id for t in result] == [t.id for t in again]
    assert len({t.id for t in result}) == 10
    categories = classify(tracks, db.liked_ids(), db.ratings(), {},
                          signals["related_ids"])
    category_of = {t.id: group for group, items in categories.items() for t in items}
    assert Counter(category_of[t.id] for t in result) == {
        "familiar": 6, "similar": 3, "new": 1
    }


def test_giant_custom_playlist_does_not_imply_every_track_is_similar(tmp_path):
    db = Library(tmp_path / "db.sqlite")
    seed = make_track(db, 1, "Seed")
    db.like(seed.id)
    playlist = db.create_playlist("All music")
    db.add_to(playlist, seed.id)
    for i in range(2, 84):
        db.add_to(playlist, make_track(db, i, "Other").id)
    signals = db.recommendation_signals(db.liked_ids())
    assert signals["related_ids"] == set()
