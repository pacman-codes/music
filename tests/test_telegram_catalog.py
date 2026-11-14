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
    assert "не Telegram-аудио" in text
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
    assert len(router.message.handlers) - before_msg == 3
    assert len(router.callback_query.handlers) - before_cb == 2
