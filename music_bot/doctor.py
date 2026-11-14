"""Read-only music bot deployment preflight.

No token, user id or private URL appears in diagnostic output. Never sends
messages, downloads media, migrates or modifies Soodoo, or writes to SQLite.
Run on the future dedicated music host after its env is configured.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import shutil
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from aiogram import Bot

from music_bot.config import Settings, load_settings


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    message: str


def _check(name: str, ok: bool, message: str, *, warning: bool = False) -> Check:
    return Check(name, "ok" if ok else ("warning" if warning else "error"), message)


def preflight(settings: Settings) -> list[Check]:
    """Inspect dependencies and metadata without changing local state."""
    checks = [
        _check("bot_token", bool(settings.token), "Token configured" if settings.token
               else "Set BOT_TOKEN in private .env"),
        _check("owner_id", settings.owner_id > 0,
               "Owner ID configured" if settings.owner_id > 0
               else "Set positive OWNER_TELEGRAM_ID"),
    ]
    if settings.webapp_url:
        checks.append(_check(
            "mini_app_https", settings.webapp_url.startswith("https://"),
            "HTTPS Mini App URL configured" if settings.webapp_url.startswith("https://")
            else "Mini App URL must use HTTPS",
        ))
    else:
        checks.append(_check(
            "mini_app_https", False,
            "Mini App HTTPS not set; bot commands still work without it",
            warning=True,
        ))
    db_path = settings.db_path
    exists = db_path.is_file()
    checks.append(_check(
        "database", exists,
        "SQLite database exists" if exists else
        "New installation: SQLite database will be created on first bot start",
        warning=not exists,
    ))
    if exists:
        # URI=ro: the diagnostic must not create a new database or schema.
        try:
            with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro",
                                 uri=True) as conn:
                integrity = conn.execute("PRAGMA quick_check").fetchone()
                rows = conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master "
                    "WHERE type='table' AND name='tracks'"
                ).fetchone()[0]
                tracks = conn.execute(
                    "SELECT COUNT(*) FROM tracks"
                ).fetchone()[0] if rows else 0
            checks.append(_check(
                "database_integrity",
                integrity is not None and integrity[0] == "ok",
                "SQLite quick_check passed; tracks: " + str(tracks)
                if integrity is not None and integrity[0] == "ok"
                else "SQLite quick_check failed; restore a verified backup",
            ))
        except (sqlite3.DatabaseError, OSError):
            checks.append(_check(
                "database_integrity", False,
                "Could not read SQLite database; check permissions and backups",
            ))
    parent = db_path.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    try:
        free = shutil.disk_usage(parent).free
    except OSError:
        free = 0
    checks.append(_check(
        "disk_free", free >= 200 * 1024 * 1024,
        "At least 200 MiB free for temporary downloads and database backups"
        if free >= 200 * 1024 * 1024 else
        "Under 200 MiB free; downloading music may fill the server disk",
    ))
    if settings.youtube_search_enabled:
        checks.append(_check(
            "yt_dlp",
            importlib.util.find_spec("yt_dlp") is not None,
            "yt-dlp installed" if importlib.util.find_spec("yt_dlp") is not None
            else "Build with MUSIC_WITH_YOUTUBE=true (yt-dlp missing)",
        ))
    if settings.youtube_download_enabled:
        checks.append(_check(
            "ffmpeg", shutil.which("ffmpeg") is not None,
            "FFmpeg installed" if shutil.which("ffmpeg") is not None
            else "FFmpeg missing; YouTube MP3 conversion cannot work",
        ))
        checks.append(_check(
            "ffprobe", shutil.which("ffprobe") is not None,
            "ffprobe installed" if shutil.which("ffprobe") is not None
            else "ffprobe missing; verify media tooling",
        ))
    checks.append(_check(
        "youtube_search", True,
        "Enabled (live site access unverified)" if settings.youtube_search_enabled
        else "Disabled by default",
    ))
    checks.append(_check(
        "youtube_download", True,
        "Enabled (only download authorized media; live test required)"
        if settings.youtube_download_enabled else "Disabled by default",
    ))
    if settings.acoustid_client_key:
        checks.append(_check(
            "fpcalc", shutil.which("fpcalc") is not None,
            "Chromaprint fpcalc installed" if shutil.which("fpcalc") is not None
            else "Missing fpcalc: build with MUSIC_WITH_FINGERPRINT=true",
        ))
    checks.append(_check(
        "tmdb", True, "Configured" if settings.tmdb_read_token
        else "Disabled; movie lookup requires its own TMDB token",
    ))
    return checks


async def telegram_probe(settings: Settings) -> list[Check]:
    """Optional read-only Bot API calls, never sends a message."""
    try:
        async with Bot(token=settings.token) as bot:
            identity = await bot.get_me()
            if not identity.is_bot:
                return [_check("telegram_auth", False, "Bot API identity is not a bot")]
            checks = [_check("telegram_auth", True,
                             "Telegram Bot API accepted the configured token")]
            try:
                await bot.get_chat(settings.owner_id)
                checks.append(_check(
                    "owner_chat", True, "Owner chat can be accessed",
                ))
            except Exception:
                checks.append(_check(
                    "owner_chat", False,
                    "Start this bot once in a private chat before uploading audio",
                    warning=True,
                ))
            return checks
    except Exception:
        # Errors may contain sensitive HTTP URL details; never print exception.
        return [_check(
            "telegram_auth", False,
            "Bot API check failed: verify token, DNS and outbound HTTPS",
        )]


def main():
    parser = argparse.ArgumentParser(
        description="Read-only music deployment checks; --online checks Telegram only"
    )
    parser.add_argument("--online", action="store_true",
                        help="Check bot token and private chat with Telegram API")
    parser.add_argument("--json", action="store_true",
                        help="Output machine-readable check results, without secrets")
    args = parser.parse_args()
    try:
        settings = load_settings()
    except RuntimeError:
        checks = [Check("configuration", "error",
                        "Missing or invalid private configuration; check .env")]
    else:
        checks = preflight(settings)
        if args.online:
            checks.extend(asyncio.run(telegram_probe(settings)))
    if args.json:
        print(json.dumps([asdict(check) for check in checks], ensure_ascii=False))
    else:
        for item in checks:
            print(f"{item.status.upper():7s} {item.name}: {item.message}")
    if any(item.status == "error" for item in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
