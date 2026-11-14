"""Scheduled weekly notifier is opt-in, atomic, and retries failed sends."""
import asyncio
from datetime import date

import pytest

from music_bot.storage import Library
from music_bot.weekly_notify import (
    format_digest, send_weekly_digest, week_key,
)

TODAY=date(2026,11,14)
MBID="8a49dba0-253a-4535-b87f-78bb035336ce"


class FakeCatalog:
    async def _json(self,provider,path,params,ttl):
        return {"payload":{"releases":[{
            "release_group_mbid":MBID,
            "release_name":"New Album",
            "artist_credit_name":"Band",
            "release_date":"2026-11-13",
        }]}}


def test_digest_send_once_and_subsequent_calls_skip(tmp_path):
    db=Library(tmp_path/"music.db")
    messages=[]
    async def publisher(value):
        messages.append(value)

    async def run():
        catalog=FakeCatalog()
        sent=await send_weekly_digest(db,catalog,publisher,today=TODAY)
        again=await send_weekly_digest(db,catalog,publisher,today=TODAY)
        assert sent=={"week_key":"2026-W46","sent":True}
        assert again=={"week_key":"2026-W46","sent":False,
                       "reason":"already_sent"}
        assert len(messages)==1
        assert "New Album" in messages[0]
        assert "не аудиофайлы" in messages[0]
        assert db.weekly_digest_sent("2026-W46")
    asyncio.run(run())


def test_failed_delivery_is_not_marked_and_can_be_retried(tmp_path):
    db=Library(tmp_path/"db.sqlite")
    attempt=[]
    async def publisher(text):
        attempt.append(text)
        if len(attempt)==1:
            raise RuntimeError("Telegram network timeout")
    async def run():
        with pytest.raises(RuntimeError):
            await send_weekly_digest(db,FakeCatalog(),publisher,today=TODAY)
        assert not db.weekly_digest_sent("2026-W46")
        success=await send_weekly_digest(db,FakeCatalog(),publisher,today=TODAY)
        assert success["sent"] is True
        assert len(attempt)==2
    asyncio.run(run())


def test_concurrent_scheduler_returns_busy_without_duplicate_message(tmp_path):
    db1=Library(tmp_path/"db.sqlite")
    db2=Library(db1.path)
    entered,finish=asyncio.Event(),asyncio.Event()
    messages=[]
    async def slow(text):
        messages.append(text)
        entered.set()
        await finish.wait()
    async def run():
        first=asyncio.create_task(
            send_weekly_digest(db1,FakeCatalog(),slow,today=TODAY)
        )
        await entered.wait()
        blocked=await send_weekly_digest(db2,FakeCatalog(),slow,today=TODAY)
        assert blocked["reason"]=="in_progress"
        finish.set()
        await first
        assert len(messages)==1
    asyncio.run(run())
