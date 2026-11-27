"""Offline coverage of chat playlist and queue control helpers."""
import pytest

from music_bot.main import (
    create_router, home_keyboard, playlist_page, playlist_track_order, track_keyboard,
)
from music_bot.storage import Library


def callbacks(markup):
    return {b.callback_data for row in markup.inline_keyboard for b in row}


def test_playlist_pages_and_play_from_track(tmp_path):
    db = Library(tmp_path / "music.db")
    pid = db.create_playlist("Night")
    tracks = [db.add_track(f"f{i}", f"u{i}", f"Track {i}", "Artist") for i in range(12)]
    for track in tracks:
        db.add_to(pid, track.id)
    text, markup = playlist_page(db, pid)
    assert "12 треков" in text and "1/2" in text
    assert f"plopen:{pid}:1" in callbacks(markup)
    assert f"plstart:{pid}:shuffle" in callbacks(markup)
    text, markup = playlist_page(db, pid, 1)
    assert "2/2" in text
    assert f"plopen:{pid}:0" in callbacks(markup)
    assert f"plstart:{pid}:{tracks[8].id}" in callbacks(markup)
    assert f"pltrack:{pid}:{tracks[8].id}:1" in callbacks(markup)
    ids = [t.id for t in tracks]
    assert playlist_track_order(db, pid, str(ids[8])) == ids[8:]
    assert sorted(playlist_track_order(db, pid, "shuffle")) == sorted(ids)
    assert playlist_track_order(db, pid) == ids
    with pytest.raises(ValueError):
        playlist_track_order(db, pid, "99999")
    assert db.shift_playlist_track(pid, ids[8], -1)
    assert playlist_track_order(db, pid)[7:9] == [ids[8], ids[7]]
    assert not db.shift_playlist_track(pid, ids[0], -1)
    db.remove_from(pid, ids[8])
    assert db.get_track(ids[8]) is not None
    assert ids[8] not in playlist_track_order(db, pid)


def test_empty_playlist_and_protected_liked_controls(tmp_path):
    db = Library(tmp_path / "music.db")
    pid = db.create_playlist("Empty")
    text, markup = playlist_page(db, pid, 20)
    assert "Пока пусто" in text and "1/1" in text
    assert f"plrename:{pid}" in callbacks(markup)
    assert f"pldelete:{pid}" in callbacks(markup)
    _, liked = playlist_page(db, db.liked_id())
    assert not any(c.startswith(("pldelete:", "plrename:")) for c in callbacks(liked))
    assert "nav:queue" in callbacks(home_keyboard())
    assert "enqueue:next:42" in callbacks(track_keyboard(42))
    # Register actual aiogram filters as well as rendering helpers.
    assert create_router(db, 42)


def test_stale_queue_buttons_cannot_remove_another_track(tmp_path):
    db = Library(tmp_path / "music.db")
    tracks = [db.add_track(f"f{i}", f"u{i}", f"Track {i}", "Artist") for i in range(3)]
    qid = db.create_queue([t.id for t in tracks], "Queue", "playlist")
    db.edit_queue(qid, "move", position=0, to_position=1,
                  expected_cursor=0, expected_track_id=tracks[0].id)
    with pytest.raises(ValueError):
        db.edit_queue(qid, "remove", position=0, expected_cursor=0,
                      expected_track_id=tracks[0].id)
    assert [row["track_id"] for row in db.queue_snapshot(qid)["upcoming"]] == [
        tracks[1].id, tracks[0].id, tracks[2].id,
    ]
    db.edit_queue(qid, "remove", position=0, expected_cursor=0,
                  expected_track_id=tracks[1].id)
    assert db.get_track(tracks[1].id) is not None


def test_navigation_reuses_only_text_panels_and_preserves_receipts():
    import asyncio
    from music_bot.main import render_chat_panel

    class Message:
        def __init__(self, text=None, audio=None):
            self.text = text
            self.audio = audio
            self.edits = []
            self.answers = []

        async def edit_text(self, text, **kwargs):
            self.edits.append((text, kwargs))

        async def answer(self, text, **kwargs):
            self.answers.append((text, kwargs))

    markup = home_keyboard()
    async def scenario():
        for source in ("🎵 LAIN · Твоя медиатека", "📂 Night · 12 треков",
                       "🎵 Трек · Artist — Track", "🧭 Реакция · Artist — Track",
                       "⭐ Оценка · Artist — Track", "⏭ Очередь #1",
                       "💿 Мои альбомы", "🧭 Список · Добавлю"):
            message = Message(text=source)
            await render_chat_panel(message, "Destination", markup, edit=True)
            assert message.edits == [("Destination", {"reply_markup": markup})]
            assert not message.answers

        for source in ("🎧 Playlist: отправлено 10, осталось 3.",
                       "✅ Playlist: последние 3 треков отправлены.",
                       "ordinary text", None):
            message = Message(text=source)
            await render_chat_panel(message, "Destination", markup, edit=True)
            assert not message.edits
            assert len(message.answers) == 1

        audio = Message(text="📂 Night", audio=object())
        await render_chat_panel(audio, "Destination", markup, edit=True)
        assert not audio.edits
        assert len(audio.answers) == 1

        command = Message(text="📂 Night")
        await render_chat_panel(command, "Destination", markup)
        assert not command.edits
        assert len(command.answers) == 1

    asyncio.run(scenario())


def test_navigation_falls_back_when_telegram_rejects_edit():
    import asyncio
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import EditMessageText
    from music_bot.main import render_chat_panel

    class Message:
        text = "🎵 LAIN · Твоя медиатека"
        audio = None

        def __init__(self, error):
            self.error = error
            self.answers = []

        async def edit_text(self, text, **kwargs):
            raise TelegramBadRequest(
                method=EditMessageText(text=text, chat_id=42, message_id=1),
                message=self.error)

        async def answer(self, text, **kwargs):
            self.answers.append(text)

    markup = home_keyboard()
    expired = Message("Bad Request: message can't be edited")
    asyncio.run(render_chat_panel(expired, "Destination", markup, edit=True))
    assert expired.answers == ["Destination"]
    same = Message("Bad Request: message is not modified")
    asyncio.run(render_chat_panel(same, "Destination", markup, edit=True))
    assert not same.answers
