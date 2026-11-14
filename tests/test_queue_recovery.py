"""Recover already-delivered audio when Telegram loses the companion controls.

Two independent workers must not send the same queue batch concurrently.
"""
from __future__ import annotations

import asyncio

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from music_bot.main import deliver_queue, home_keyboard
from music_bot.storage import Library


class FakeTelegram:
    def __init__(self, *, lose_first_control=False):
        self.groups = []
        self.audio = []
        self.messages = []
        self.lose_first_control = lose_first_control

    async def send_audio(self, *args, **kwargs):
        self.audio.append((args, kwargs))

    async def send_media_group(self, *args, **kwargs):
        self.groups.append((args, kwargs))

    async def send_message(self, *args, **kwargs):
        if self.lose_first_control and kwargs.get("reply_markup"):
            self.lose_first_control = False
            raise TelegramBadRequest(
                method=SendMessage(chat_id=42, text="control card"),
                message="Bad Request: cannot send controls",
            )
        self.messages.append((args, kwargs))


def test_latest_queue_id_and_recovery_from_missing_control_card(tmp_path):
    path = tmp_path / "music.db"
    db = Library(path)
    assert db.latest_queue_id() is None
    tracks = [
        db.add_track("file-" + str(i), "unique-" + str(i),
                     "Title " + str(i), "Artist")
        for i in range(12)
    ]
    queue_id = db.create_queue([t.id for t in tracks], "My playlist", "playlist")
    assert Library(path).latest_queue_id() == queue_id
    bot = FakeTelegram(lose_first_control=True)

    async def run():
        # Telegram accepts the 10 audio messages but rejects the control card.
        await deliver_queue(bot, db, 42, queue_id, asyncio.Lock())
        assert len(bot.groups) == 1
        assert db.peek_queue(queue_id).cursor == 10
        assert len(bot.messages) == 0

        # /continue looks up the latest persisted queue ID and sends the next
        # batch, not the first ten already received by Telegram.
        saved = Library(path).latest_queue_id()
        await deliver_queue(bot, db, 42, saved, asyncio.Lock())
        assert len(bot.groups) == 2
        assert db.peek_queue(queue_id).cursor == 12
        assert db.recent_ids() == {t.id for t in tracks}
        assert bot.messages[-1][1]["reply_markup"]
        assert len(bot.groups[-1][1]["media"]) == 2

    asyncio.run(run())
    assert any(
        x.callback_data == "nav:continue"
        for group in home_keyboard().inline_keyboard for x in group
    )


def test_two_independent_workers_share_queue_send_lock(tmp_path):
    path = tmp_path / "music.db"
    db = Library(path)
    ids = [
        db.add_track("file-" + str(i), "unique-" + str(i),
                     "Title " + str(i), "Artist").id
        for i in range(13)
    ]
    queue_id = db.create_queue(ids, "My playlist", "playlist")
    started, release = asyncio.Event(), asyncio.Event()

    class SlowTelegram(FakeTelegram):
        async def send_media_group(self, *args, **kwargs):
            started.set()
            await release.wait()
            await super().send_media_group(*args, **kwargs)

    first_bot = SlowTelegram()
    second_bot = FakeTelegram()

    async def run():
        first = asyncio.create_task(
            deliver_queue(first_bot, db, 42, queue_id, asyncio.Lock())
        )
        await started.wait()
        # Independent process (Mini App) uses its own Library and Lock, but
        # the same SQLite lease. It must never duplicate the first batch.
        await deliver_queue(
            second_bot, Library(path), 42, queue_id, asyncio.Lock(),
        )
        assert not second_bot.groups
        assert len(second_bot.messages) == 1
        assert "уже отправляется" in second_bot.messages[0][0][1]
        release.set()
        await first
        assert db.peek_queue(queue_id).cursor == 10
        assert len(first_bot.groups) == 1
        assert not second_bot.groups
        with db.con() as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM media_import_leases WHERE source_type='queue'"
            ).fetchone()[0] == 0

    asyncio.run(run())


def test_queue_lease_recovers_after_crash(tmp_path):
    db = Library(tmp_path / "music.db")
    key = "123"
    first = db.acquire_media_import("queue", key, now=100, ttl=180)
    assert first
    assert db.acquire_media_import("queue", key, now=150, ttl=180) is None
    second = db.acquire_media_import("queue", key, now=281, ttl=180)
    assert second and second != first
    assert db.release_media_import("queue", key, first) is False
    assert db.release_media_import("queue", key, second) is True
