"""Native Telegram /import command; works without a Mini App HTTPS endpoint.

Only a previously discovered MusicBrainz MBID and a host explicitly
allowlisted in local .env are allowed to enter the media-ingest path.
"""
from __future__ import annotations

import asyncio

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message

from music_bot.catalog import valid_mbid
from music_bot.importer import AudioIngestService, MediaImportError
from music_bot.storage import Library


def parse_import_command(text: str) -> tuple[str, str] | None:
    if not isinstance(text, str):
        return None
    tokens = text.strip().split(maxsplit=2)
    if len(tokens) != 3:
        return None
    if not tokens[0].split("@")[0].lower() == "/import":
        return None
    mbid, url = tokens[1].lower(), tokens[2].strip()
    if not valid_mbid(mbid) or not url or len(url) > 2048:
        return None
    return mbid, url


def install_import_handlers(router: Router, db: Library,
                            owner_id: int, allowed_hosts: tuple[str, ...]) -> None:
    lock = asyncio.Lock()

    @router.message(Command("import"))
    async def import_audio(message: Message, bot: Bot):
        parsed = parse_import_command(message.text or "")
        if parsed is None:
            await message.answer(
                "Пример: /import MBID https://разрешённый-хост/track.mp3\n"
                "Сначала найди запись через /find. Ссылки только прямые MP3/M4A."
            )
            return
        if not allowed_hosts:
            await message.answer(
                "Импорт по ссылке отключён. Нужна настройка "
                "MUSIC_ALLOWED_MEDIA_HOSTS в локальном .env сервера."
            )
            return
        mbid, url = parsed

        async def publish(path, title, artist):
            sent = await bot.send_audio(
                chat_id=owner_id, audio=FSInputFile(path),
                title=title[:64], performer=artist[:64],
            )
            if sent.audio is None:
                raise MediaImportError("Telegram не вернул аудиофайл")
            return sent.audio.file_id, sent.audio.file_unique_id

        ingest = AudioIngestService(db, allowed_hosts, publish)
        async with lock:
            try:
                result = await ingest.import_recording(mbid, url)
            except MediaImportError as exc:
                await message.answer(f"Не удалось импортировать: {str(exc)[:180]}")
                return
            except TelegramAPIError:
                await message.answer(
                    "Telegram не принял аудиофайл; повтори запрос позже."
                )
                return
        if result["reused"]:
            track = db.get_track(result["track_id"])
            if track:
                await bot.send_audio(
                    chat_id=owner_id, audio=track.file_id,
                    title=track.title[:64], performer=track.artist[:64],
                )
            return
        # New audio was already sent to the owner by the publisher.
        await message.answer(
            "🎵 Трек загружен в Telegram и добавлен в библиотеку. "
            "Теперь его можно включать в /wave."
        )
