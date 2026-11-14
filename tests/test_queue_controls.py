"""Single companion keyboard for each native Telegram audio batch."""
import asyncio

from music_bot.main import deliver_queue, queue_feedback_keyboard
from music_bot.storage import Library
from tests.test_bot import FakeBot


def controls(keyboard):
    return [b for row in keyboard.inline_keyboard for b in row]


def test_batch_keyboard_has_ten_track_choices_and_more(tmp_path):
    db = Library(tmp_path / "music.db")
    songs = [
        db.add_track(f"file_{i}", f"unique_{i}", f"Song {i}", "Artist")
        for i in range(13)
    ]
    queue_id = db.create_queue([t.id for t in songs], "Test Wave", "wave")
    bot = FakeBot()

    async def exercise():
        await deliver_queue(bot, db, 123, queue_id, asyncio.Lock())
        assert len(bot.groups) == 1
        assert len(bot.messages) == 1  # Not one control card per song.
        card = bot.messages[-1][1]["reply_markup"]
        buttons = controls(card)
        first_batch_ids = {str(t.id) for t in songs[:10]}
        ratings = [x.callback_data for x in buttons if x.callback_data.startswith("rateopen:")]
        assert len(ratings) == 10
        assert {x.split(":")[1] for x in ratings} == first_batch_ids
        assert any(b.callback_data == f"more:{queue_id}" for b in buttons)
        await deliver_queue(bot, db, 123, queue_id, asyncio.Lock())
        last_buttons = controls(bot.messages[-1][1]["reply_markup"])
        assert len([b for b in last_buttons if b.callback_data.startswith("rateopen:")]) == 3
        assert not any(b.callback_data.startswith("more:") for b in last_buttons)

    asyncio.run(exercise())


def test_track_access_and_long_title_truncation(tmp_path):
    db = Library(tmp_path / "music.db")
    t = db.add_track("file", "unique", "X"*200, "Artist")
    assert db.get_track(t.id) == t
    assert db.get_track(98293) is None
    markup = queue_feedback_keyboard((t,), 10, False)
    items = controls(markup)
    assert items[0].callback_data == f"rateopen:{t.id}"
    assert len(items[0].text) < 35
