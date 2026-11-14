"""Single companion keyboard for each native Telegram audio batch."""
import asyncio

from music_bot.main import deliver_queue, queue_feedback_keyboard, parse_more_callback
from music_bot.storage import Library
class FakeBot:
    def __init__(self):
        self.groups = []
        self.messages = []

    async def send_audio(self, *args, **kwargs):
        pass

    async def send_media_group(self, *args, **kwargs):
        self.groups.append((args, kwargs))

    async def send_message(self, *args, **kwargs):
        self.messages.append((args, kwargs))


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
        assert any(b.callback_data == f"more:{queue_id}:10" for b in buttons)
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


def test_continuation_callback_requires_queue_cursor(tmp_path):
    assert parse_more_callback("more:5:10") == (5, 10)
    for invalid in ("more:5", "more:5:0", "more:0:10",
                    "more:5:wrong", "more:5:10:20", "more:1:-5"):
        assert parse_more_callback(invalid) is None

    db = Library(tmp_path / "db.sqlite")
    songs = [db.add_track(f"file_{i}", f"u_{i}", f"Title {i}", "Artist")
             for i in range(23)]
    queue_id = db.create_queue([t.id for t in songs], "Playlist", "playlist")
    bot = FakeBot()

    async def check():
        lock = asyncio.Lock()
        await deliver_queue(bot, db, 42, queue_id, lock)
        first = bot.messages[-1][1]["reply_markup"]
        first_button = next(
            b for b in controls(first) if b.callback_data.startswith("more:")
        )
        assert parse_more_callback(first_button.callback_data) == (queue_id, 10)

        await deliver_queue(
            bot, db, 42, queue_id, lock, expected_cursor=10,
        )
        assert len(bot.groups) == 2
        cursor = db.peek_queue(queue_id).cursor
        assert cursor == 20
        # Double-click or pressing the old card must not send the third batch.
        await deliver_queue(
            bot, db, 42, queue_id, lock, expected_cursor=10,
        )
        assert db.peek_queue(queue_id).cursor == 20
        assert len(bot.groups) == 2
        last = bot.messages[-2][1]["reply_markup"]
        next_button = next(
            b for b in controls(last) if b.callback_data.startswith("more:")
        )
        assert parse_more_callback(next_button.callback_data) == (queue_id, 20)
        await deliver_queue(
            bot, db, 42, queue_id, lock, expected_cursor=20,
        )
        assert len(bot.groups) == 3
    asyncio.run(check())

