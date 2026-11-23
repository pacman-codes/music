"""Telegram getMe flood waits instead of crashing into a Docker restart loop."""
import asyncio
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import GetMe

from music_bot import main as music_main


def test_getme_flood_control_waits_for_retry_after(monkeypatch):
    delays = []
    calls = []

    class FakeBot:
        async def me(self):
            calls.append(1)
            if len(calls) <= 2:
                raise TelegramRetryAfter(
                    method=GetMe(), message="Too Many Requests",
                    retry_after=3 if len(calls) == 1 else 12,
                )
            return {"id": 123}

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(music_main.asyncio, "sleep", fake_sleep)
    asyncio.run(music_main.await_telegram_ready(FakeBot()))
    assert len(calls) == 3
    assert delays == [4, 13]


def test_getme_success_has_no_sleep(monkeypatch):
    async def fail_sleep(_delay):
        raise AssertionError("Unexpected sleep after a successful getMe")

    class FakeBot:
        async def me(self):
            return {"id": 123}

    monkeypatch.setattr(music_main.asyncio, "sleep", fail_sleep)
    asyncio.run(music_main.await_telegram_ready(FakeBot()))
