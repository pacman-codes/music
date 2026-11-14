"""Five exclusive, owner-controlled music decision playlists.

The same decision can be applied to real Telegram media or catalog metadata
without ever claiming that a MusicBrainz ID is directly playable.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton
import pytest

from music_bot.catalog import CatalogTrack
from music_bot.main import home_keyboard, queue_feedback_keyboard, track_keyboard
from music_bot.selection import select_wave_batch
from music_bot.storage import Library
from music_bot.telegram_catalog import catalog_keyboard
from music_bot.verdicts import LABELS, STATUSES, yandex_search_url


def test_five_way_local_decisions_are_exclusive_and_sync_likes(tmp_path):
    db = Library(tmp_path / "music.db")
    track = db.add_track("tg_file_secret", "unique", "Breakbeat", "Artist")
    assert len(STATUSES) == 5
    for status in STATUSES:
        db.set_track_verdict(track.id, status)
        assert db.track_verdicts() == {track.id: status}
        active = [
            name for name in STATUSES
            if any(item["track_id"] == track.id for item in db.verdict_items(name))
        ]
        assert active == [status]
        assert (track.id in db.liked_ids()) == (status == "add")
        assert (track.id in db.disliked_ids()) == (status == "never")
    assert sum(db.verdict_counts().values()) == 1
    with pytest.raises(ValueError):
        db.set_track_verdict(track.id, "bad")
    with pytest.raises(ValueError):
        db.set_track_verdict(999, "add")
    assert db.track_verdicts() == {track.id: "interesting"}


def test_catalog_decisions_appear_without_telegram_audio(tmp_path):
    db = Library(tmp_path / "music.db")
    mbid = "8a49dba0-253a-4535-b87f-78bb035336ce"
    record = CatalogTrack(mbid, "Crowded Roomz", "Nia Archives")
    db.remember_candidates([record])
    db.set_catalog_verdict(mbid, "maybe")
    entries = db.verdict_items("maybe")
    assert len(entries) == 1
    assert entries[0]["source"] == "catalog"
    assert entries[0]["track_id"] is None
    assert entries[0]["mbid"] == mbid
    assert entries[0]["title"] == "Crowded Roomz"
    db.set_catalog_verdict(mbid, "interesting")
    assert not db.verdict_items("maybe")
    assert len(db.verdict_items("interesting")) == 1
    assert db.liked_ids() == set()
    with pytest.raises(ValueError):
        db.set_catalog_verdict("00000000-0000-0000-0000-000000000000", "add")
    with pytest.raises(ValueError):
        db.set_catalog_verdict(mbid, "not_an_option")


def test_local_and_catalog_identity_decision_is_shared_when_confirmed(tmp_path):
    db = Library(tmp_path / "music.db")
    mbid = "8a49dba0-253a-4535-b87f-78bb035336ce"
    record = CatalogTrack(mbid, "Roads", "Portishead")
    db.remember_candidates([record])
    db.set_catalog_verdict(mbid, "maybe")
    track = db.add_track("tg_file", "uid", "Roads", "Portishead")
    db.confirm_recording_match(track.id, mbid)
    db.set_catalog_verdict(mbid, "add")
    assert db.track_verdicts()[track.id] == "add"
    assert track.id in db.liked_ids()
    assert len(db.verdict_items("add")) == 1
    item = db.verdict_items("add")[0]
    assert item["track_id"] == track.id
    assert item["source"] == "local"
    db.set_track_verdict(track.id, "later")
    assert db.catalog_verdicts()[mbid] == "later"
    assert len(db.verdict_items("later")) == 1
    assert track.id not in db.liked_ids()


@pytest.mark.parametrize("status", ["later", "interesting", "never"])
def test_parked_tracks_excluded_from_auto_wave_but_remain_playable(status, tmp_path):
    db = Library(tmp_path / "music.db")
    ignored = db.add_track("file1", "uid1", "Nope", "One")
    yes = db.add_track("file2", "uid2", "Okay", "Two")
    db.set_track_verdict(ignored.id, status)
    db.set_track_verdict(yes.id, "add")
    assert ignored.id in db.suppressed_verdict_track_ids()
    for mode in ("balanced", "discover", "favorites"):
        chosen = select_wave_batch(db, mode, count=2)
        assert ignored.id not in {t.id for t in chosen}
    assert db.get_track(ignored.id).file_id == "file1"
    assert ignored.id in [t.id for t in db.all_tracks()]


def test_maybe_remains_eligible_for_wave(tmp_path):
    db = Library(tmp_path / "music.db")
    track = db.add_track("file", "uid", "Maybe", "Some Artist")
    db.set_track_verdict(track.id, "maybe")
    assert db.suppressed_verdict_track_ids() == set()
    assert [t.id for t in select_wave_batch(db, "balanced")] == [track.id]


def test_legacy_like_dislike_actions_stay_consistent(tmp_path):
    db = Library(tmp_path / "music.db")
    t = db.add_track("file", "uid", "Song", "Artist")
    assert db.like(t.id)
    assert db.track_verdicts()[t.id] == "add"
    db.dislike(t.id)
    assert db.track_verdicts()[t.id] == "never"
    assert t.id not in db.liked_ids()
    assert db.like(t.id)
    assert db.track_verdicts()[t.id] == "add"
    assert db.unlike(t.id)
    assert t.id not in db.track_verdicts()


def test_five_choice_controls_fit_telegram_callback_data_limits():
    track_buttons = [
        btn for row in track_keyboard(123).inline_keyboard for btn in row
    ]
    assert {
        btn.callback_data.rsplit(":", 1)[1] for btn in track_buttons
        if btn.callback_data and btn.callback_data.startswith("vset:")
    } == set(STATUSES)
    assert all(
        len(btn.callback_data.encode("utf-8")) <= 64
        for btn in track_buttons if btn.callback_data
    )
    catalog = catalog_keyboard([
        CatalogTrack("8a49dba0-253a-4535-b87f-78bb035336ce",
                     "Song", "Artist")
    ])
    cb = [b.callback_data for row in catalog.inline_keyboard for b in row]
    assert "vopen:c:8a49dba0-253a-4535-b87f-78bb035336ce" in cb
    choices = [
        b.callback_data for row in
        queue_feedback_keyboard((type("Track", (), {"id": 1, "title": "A"})(),),
                                1, False).inline_keyboard for b in row
    ]
    assert "vopen:l:1" in choices
    nav = [btn.callback_data for row in home_keyboard().inline_keyboard for btn in row]
    assert "nav:verdicts" in nav


def test_yandex_search_link_does_not_claim_tg_playback():
    link = yandex_search_url("Nia Archives", "Forbidden Feelingz")
    assert link.startswith("https://music.yandex.ru/search?text=")
    assert "Nia%20Archives%20Forbidden%20Feelingz" in link
    assert "file_id" not in link

def test_private_profile_exports_all_five_decisions_and_changes(tmp_path):
    from music_bot.profile_export import export_profile, compare_profiles, profile_json_bytes

    db = Library(tmp_path / "music.db")
    track = db.add_track("SHOULD_NOT_LEAK", "UNIQUE_SHOULD_NOT_LEAK", "Song", "Artist")
    mbid = "8a49dba0-253a-4535-b87f-78bb035336ce"
    db.remember_candidates([CatalogTrack(mbid, "Outside", "Other")])
    previous = export_profile(db)
    db.set_track_verdict(track.id, "interesting")
    db.set_catalog_verdict(mbid, "later")
    current = export_profile(db)
    assert current["tracks"][0]["decision"] == "interesting"
    assert current["catalog_decisions"][0]["decision"] == "later"
    delta = compare_profiles(previous, current)
    assert delta["decision_changes"] == [
        {"id": track.id, "before": None, "after": "interesting"}
    ]
    assert delta["catalog_decision_changes"][0]["mbid"] == mbid
    encoded = profile_json_bytes(current).decode()
    assert "SHOULD_NOT_LEAK" not in encoded
    assert "UNIQUE_SHOULD_NOT_LEAK" not in encoded
