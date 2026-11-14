"""Offline Bot API tests: no credentials, no calls to Telegram."""
import asyncio
from types import SimpleNamespace

from music_bot.main import OwnerOnly, deliver_queue
from music_bot.storage import Library


class FakeBot:
    def __init__(self):
        self.audio = []
        self.groups = []
        self.messages = []

    async def send_audio(self, *args, **kwargs):
        self.audio.append((args, kwargs))

    async def send_media_group(self, *args, **kwargs):
        self.groups.append((args, kwargs))

    async def send_message(self, *args, **kwargs):
        self.messages.append((args, kwargs))


def test_queue_sent_in_native_audio_batches(tmp_path):
    db = Library(tmp_path / "music.db")
    ts = [db.add_track(f"file-{i}", f"uid-{i}", f"Track {i}", "Artist")
          for i in range(23)]
    queue = db.create_queue([t.id for t in ts], "My wave", "wave")
    bot = FakeBot()

    async def scenario():
        lock = asyncio.Lock()
        await deliver_queue(bot, db, 12345, queue, lock)
        assert len(bot.groups) == 1
        assert len(bot.groups[0][1]["media"]) == 10
        assert len(db.recent_ids()) == 10
        await deliver_queue(bot, db, 12345, queue, lock)
        assert len(bot.groups) == 2
        await deliver_queue(bot, db, 12345, queue, lock)
        assert len(bot.groups) == 3
        assert len(bot.groups[-1][1]["media"]) == 3
        assert db.peek_queue(queue).remaining_after == 0
        assert not db.peek_queue(queue).tracks
        await deliver_queue(bot, db, 12345, queue, lock)
        assert len(bot.groups) == 3
        assert len(bot.messages) >= 4

    asyncio.run(scenario())


def test_single_track_uses_send_audio(tmp_path):
    db = Library(tmp_path / "music.db")
    t = db.add_track("file", "unique", "Solo", "Artist")
    q = db.create_queue([t.id], "Only one", "playlist")
    bot = FakeBot()
    asyncio.run(deliver_queue(bot, db, 5, q, asyncio.Lock()))
    assert len(bot.audio) == 1
    assert len(bot.groups) == 0


def test_owner_only_rejects_unauthorized_or_nonprivate():
    owner = OwnerOnly(4242)
    called = []

    async def handler(event, data):
        called.append(True)
        return "allowed"

    async def trial(user_id, chat_type):
        data = {
            "event_from_user": SimpleNamespace(id=user_id),
            "event_chat": SimpleNamespace(type=chat_type),
        }
        return await owner(handler, object(), data)

    assert asyncio.run(trial(1234, "private")) is None
    assert asyncio.run(trial(4242, "group")) is None
    assert asyncio.run(trial(4242, "private")) == "allowed"
    assert len(called) == 1
