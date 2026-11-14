"""Opt-in once-per-week Telegram digest sender, safe to invoke from cron.

No daemon is started by Docker Compose. Run --send explicitly from a trusted
scheduler on the future music host. A receipt is written ONLY after Telegram
acknowledges the owner-only message; transient failure remains retryable.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timezone
from typing import Awaitable, Callable

from aiogram import Bot

from music_bot.catalog import MusicCatalog
from music_bot.config import load_settings
from music_bot.storage import Library
from music_bot.weekly import weekly_discovery


def week_key(today: date) -> str:
    iso = today.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def format_digest(payload: dict) -> str:
    lines = [
        f"✨ Твои находки недели {payload['year']}/W{payload['week']:02d}",
        "Это новые музыкальные записи из каталогов, не аудиофайлы.",
    ]
    if payload["seed_artists"]:
        lines.append("На основе: " + ", ".join(payload["seed_artists"][:3]))
    if payload["releases"]:
        lines.append("💿 Свежие релизы:")
        lines.extend(
            f"• {v['artist']} — {v['name']} ({v['release_date']})"[:190]
            for v in payload["releases"][:7]
        )
    if payload["tracks"]:
        lines.append("🎶 Музыка для открытия:")
        lines.extend(
            f"• {v['artist']} — {v['title']}"[:170]
            for v in payload["tracks"][:7]
        )
    if not payload["releases"] and not payload["tracks"]:
        lines.append("Новых находок пока нет. Оцени несколько любимых песен.")
    if payload["warnings"]:
        lines.append("Часть каталогов временно не отвечала.")
    lines.append("Чтобы добавить трек, открой /weekly или Mini App.")
    return "\n".join(lines)[:3900]


async def send_weekly_digest(
    db: Library, catalog: MusicCatalog,
    publisher: Callable[[str], Awaitable[object]], *,
    today: date | None = None,
) -> dict:
    day = today or datetime.now(timezone.utc).date()
    key = week_key(day)
    if db.weekly_digest_sent(key):
        return {"week_key": key, "sent": False, "reason": "already_sent"}
    lease = db.acquire_media_import("weekly", key, ttl=600)
    if lease is None:
        return {"week_key": key, "sent": False, "reason": "in_progress"}
    try:
        # Check again after acquiring lock to avoid two parallel schedulers.
        if db.weekly_digest_sent(key):
            return {"week_key": key, "sent": False, "reason": "already_sent"}
        payload = await weekly_discovery(db, catalog, limit=8, today=day)
        await publisher(format_digest(payload))
        db.mark_weekly_digest_sent(key)
        return {"week_key": key, "sent": True}
    finally:
        db.release_media_import("weekly", key, lease)


async def main_async():
    config = load_settings()
    db = Library(config.db_path)
    async with Bot(token=config.token) as bot:
        async with MusicCatalog(config.db_path) as catalog:
            async def publisher(text: str):
                return await bot.send_message(config.owner_id, text)
            return await send_weekly_digest(db, catalog, publisher)


def main():
    parser = argparse.ArgumentParser(
        description="Explicit owner-only weekly Telegram digest sender"
    )
    parser.add_argument(
        "--send", action="store_true",
        help="Opt in to sending this week's digest to Telegram owner",
    )
    args = parser.parse_args()
    if not args.send:
        parser.error("No background sender enabled. Pass --send explicitly.")
    result = asyncio.run(main_async())
    print("WEEKLY_DIGEST_SENT" if result["sent"] else
          "WEEKLY_DIGEST_SKIPPED_" + result["reason"].upper())


if __name__ == "__main__":
    main()
