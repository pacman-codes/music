"""Telegram chat catalog UI without a web Mini App or real Bot API calls."""
from aiogram import Router

from music_bot.catalog import CatalogTrack
from music_bot.storage import Library
from music_bot.telegram_catalog import (
    catalog_keyboard, default_artist, format_catalog, install_catalog_handlers,
)

MBID = "8a49dba0-253a-4535-b87f-78bb035336ce"


def test_external_result_message_is_not_a_false_playback_queue():
    item = CatalogTrack(MBID, "Roads", "Portishead")
    text = format_catalog([item], "Новая музыка")
    assert "Portishead — Roads" in text
    assert "только для" in text
    markup = catalog_keyboard([item])
    assert markup.inline_keyboard[0][0].callback_data == f"wish:{MBID}"
    assert not any(
        b.callback_data.startswith(("play:", "audio:"))
        for row in markup.inline_keyboard for b in row
    )


def test_radio_seed_from_liked_artist(tmp_path):
    db = Library(tmp_path / "db.sqlite")
    assert default_artist(db) is None
    track = db.add_track("file", "uid", "Roads", "Portishead")
    assert db.like(track.id)
    assert default_artist(db) == "Portishead"


def test_catalog_handler_registration_with_private_parent_middleware(tmp_path):
    db = Library(tmp_path / "db.sqlite")
    router = Router()
    before_msg, before_cb = len(router.message.handlers), len(router.callback_query.handlers)

    class FakeCatalog:
        pass

    install_catalog_handlers(router, db, FakeCatalog())
    assert len(router.message.handlers) - before_msg == 7
    assert len(router.callback_query.handlers) - before_cb == 3


def test_identify_keyboard_contains_only_existing_mbid_and_track_id():
    from music_bot.catalog import CatalogTrack
    mbid="8a49dba0-253a-4535-b87f-78bb035336ce"
    markup=catalog_keyboard(
        [CatalogTrack(mbid, "Roads", "Portishead")], match_track_id=12
    )
    callbacks=[b.callback_data for row in markup.inline_keyboard for b in row]
    assert f"match:12:{mbid}" in callbacks
    assert f"wish:{mbid}" in callbacks
    assert all(len(value.encode("utf-8")) <= 64 for value in callbacks)


def test_catalog_play_buttons_only_for_confirmed_telegram_files(tmp_path):
    from music_bot.catalog import CatalogTrack
    db = Library(tmp_path / "music.db")
    known = CatalogTrack(MBID, "Roads", "Portishead")
    unlinked = CatalogTrack("f3bba4cd-8018-468b-902e-bc8f029593e5",
                            "Roads (Live)", "Portishead")
    saved = db.add_track("tg-file-id", "tg-unique-id", "Roads", "Portishead")
    db.remember_candidates([known, unlinked])

    # A matching title alone cannot make the button playable.
    without = catalog_keyboard([known, unlinked],
                               linked=db.resolved_catalog_tracks())
    assert all(
        not btn.callback_data.startswith("play:")
        for row in without.inline_keyboard for btn in row
    )

    db.confirm_recording_match(saved.id, MBID)
    linked = catalog_keyboard([known, unlinked],
                              linked=db.resolved_catalog_tracks())
    buttons = [btn.callback_data for row in linked.inline_keyboard for btn in row]
    assert f"play:{saved.id}" in buttons
    assert f"wish:{MBID}" in buttons
    assert f"wish:{unlinked.mbid}" in buttons
    assert buttons.count(f"play:{saved.id}") == 1
    assert all(len(v.encode("utf-8")) <= 64 for v in buttons)
    # When the recording is already playable, do not invite relinking it
    # as a different local track from search suggestions.
    suggestions = catalog_keyboard([known], match_track_id=999, linked=db.resolved_catalog_tracks())
    assert not any(
        btn.callback_data.startswith("match:")
        for row in suggestions.inline_keyboard for btn in row
    )
