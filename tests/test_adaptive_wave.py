"""Wave rotation and changing taste without manufactured listening events."""
from music_bot.recommend import artist_key, track_weight
from music_bot.selection import select_wave_batch, wave_profile
from music_bot.storage import Library
from music_bot.wave_v2 import classify, wave_v2
from music_bot.artists import Artist


def add(db, i, name):
    return db.add_track(f"f{i}", f"u{i}", f"Track {i}", name)


def test_next_wave_rotates_full_previous_batch(tmp_path):
    db = Library(tmp_path / "music.db")
    tracks = [add(db, i, f"Band {i%5}") for i in range(24)]
    first = select_wave_batch(db, "balanced")
    qid = db.create_queue([t.id for t in first], "Wave", "balanced")
    assert db.mark_queue_sent(db.peek_queue(qid))
    context = db.wave_recent_context()
    assert context["rotation_ids"] == {t.id for t in first}
    assert context["immediate_ids"] == {t.id for t in first[-3:]}
    assert context["previous_artists"][-1] == artist_key(first[-1].artist)
    second = select_wave_batch(db, "balanced")
    assert len(second) == 10
    assert {t.id for t in first}.isdisjoint(t.id for t in second)
    assert db.playback_stats() == {}
    assert len(db.all_tracks()) == len(tracks)


def test_diversity_across_batch_boundary_when_alternatives_exist(tmp_path):
    db = Library(tmp_path / "music.db")
    tracks = [add(db, i, "Band A" if i%2 else "Band B") for i in range(10)]
    for seed in range(10):
        result = wave_v2(tracks, set(), count=8, seed=seed,
                         previous_artists=("band a",))
        assert result[0].artist == "Band B"
        assert all(a.artist != b.artist for a,b in zip(result,result[1:]))
    # A recent window may leave a single usable artist; playback still works.
    assert len(wave_v2(tracks[:1], set(), rotation_ids={tracks[0].id},
                       immediate_ids={tracks[0].id}, count=10)) == 1


def test_preference_profile_survives_candidate_filter_and_adapts(tmp_path):
    db = Library(tmp_path / "music.db")
    first = add(db, 1, "Favorite")
    second = add(db, 2, " FAVORITE ")
    saved = add(db, 3, "Saved artist")
    other = add(db, 4, "Unrelated")
    db.set_rating(first.id, 9)
    db.remember_artists([Artist("00000000-0000-0000-0000-000000000001","Saved artist")])
    db.set_artist_saved("00000000-0000-0000-0000-000000000001",True)

    def profile():
        return wave_profile(db, db.all_tracks(), liked_ids=db.liked_ids(),
            disliked_ids=db.disliked_ids(), ratings=db.ratings(),
            playback=db.playback_stats(), suppressed=db.suppressed_verdict_track_ids(),
            fresh_positive_ids=db.wave_recent_context()["fresh_positive_ids"])

    preferred, affinity = profile()
    assert {"favorite","saved artist"} <= preferred
    assert affinity["favorite"] == 1.5
    groups = classify([second,saved,other], db.liked_ids(), db.ratings(), {},
                      preferred_artists=preferred)
    assert {t.id for t in groups["similar"]} == {second.id,saved.id}
    with db.con() as con:
        con.execute("UPDATE track_ratings SET updated_at=datetime('now','-60 days') WHERE track_id=?",(first.id,))
    assert profile()[1]["favorite"] == 1.0
    assert first.id not in db.wave_recent_context()["fresh_positive_ids"]
    db.set_rating(first.id,2)
    assert "favorite" not in profile()[0]
    db.set_rating(other.id,10)
    assert "unrelated" in profile()[0]
    assert db.playback_stats() == {}


def test_feedback_boost_does_not_override_low_rating(tmp_path):
    db = Library(tmp_path / "music.db")
    t = add(db,1,"Band")
    kwargs = dict(liked_ids=set(),favorite_artists=set(),seen_artist={})
    low = track_weight(t,**kwargs,ratings={t.id:2})
    assert track_weight(t,**kwargs,ratings={t.id:2},fresh_positive_ids={t.id},
                        artist_affinity={"band":4.5}) == low
    high = track_weight(t,**kwargs,ratings={t.id:9})
    assert track_weight(t,**kwargs,ratings={t.id:9},fresh_positive_ids={t.id},
                        artist_affinity={"band":2}) > high


def test_rotation_does_not_relax_explicit_queue_filters(tmp_path):
    db = Library(tmp_path / "music.db")
    tracks = [add(db,i,f"Band {i}") for i in range(5)]
    db.like(tracks[0].id)
    db.set_rating(tracks[0].id,9)
    db.set_rating(tracks[1].id,10)
    qid = db.create_queue([tracks[0].id],"Wave","balanced")
    db.set_queue_filters(qid,min_rating=8,liked_only=True,no_repeat_today=True)
    assert [t.id for t in select_wave_batch(db,"balanced",queue_id=qid)] == [tracks[0].id]
    assert db.mark_queue_sent(db.peek_queue(qid))
    assert select_wave_batch(db,"balanced",queue_id=qid) == []
    db.set_queue_filters(qid,min_rating=8,liked_only=True,no_repeat_today=False)
    assert [t.id for t in select_wave_batch(db,"balanced",queue_id=qid)] == [tracks[0].id]
