"""Opt-in owner-only /yt and /ytget chat integration.

Never starts a download unless MUSIC_ENABLE_YOUTUBE_DOWNLOAD is enabled.
Only search-produced video IDs, not arbitrary URLs, enter yt-dlp.
"""
from __future__ import annotations

import asyncio

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery, FSInputFile, InlineKeyboardButton,
    InlineKeyboardMarkup, Message,
)

from music_bot.storage import Library
from music_bot.youtube import (
    YouTubeError, YouTubeIngestService, YouTubeSource, validate_video_id,
)


def youtube_keyboard(videos, download_enabled: bool):
    if not download_enabled:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🎧 Скачать: {video.title[:38]}",
            callback_data=f"ytget:{video.video_id}",
        )] for video in videos[:8]
    ])


def install_youtube_handlers(router: Router, db: Library, owner_id: int, *,
                             search_enabled: bool, download_enabled: bool,
                             source: YouTubeSource | None = None) -> None:
    """Router inherits the global private-chat OwnerOnly middleware."""
    worker = source or YouTubeSource()
    download_lock = asyncio.Lock()

    @router.message(Command("yt"))
    async def search(message: Message):
        if not search_enabled:
            await message.answer("Поиск YouTube выключен в настройках бота.")
            return
        query = (message.text or "").partition(" ")[2].strip()
        if not 2 <= len(query) <= 90:
            await message.answer("Пример: /yt Portishead Roads")
            return
        try:
            videos = await worker.search(query, limit=6)
        except (YouTubeError, ValueError) as exc:
            await message.answer(f"YouTube-поиск недоступен: {str(exc)[:160]}")
            return
        if not videos:
            await message.answer("YouTube не вернул подходящих коротких роликов.")
            return
        db.remember_youtube_videos(videos)
        text = "🔎 Результаты YouTube (пока не в Telegram):\n"
        text += "\n".join(
            f"{i}. {v.channel} — {v.title} [{v.video_id}]"[:220]
            for i, v in enumerate(videos, 1)
        )
        text += "\nФайлы доступны только после загрузки с разрешения правообладателя."
        await message.answer(
            text[:3800],
            reply_markup=youtube_keyboard(videos, download_enabled),
        )

    async def fetch(bot: Bot, video_id: str, chat_id: int):
        if not download_enabled:
            await bot.send_message(chat_id, "Загрузка YouTube отключена в .env.")
            return
        async def publish(path, title, channel):
            message = await bot.send_audio(
                chat_id=owner_id, audio=FSInputFile(path),
                title=title[:64], performer=channel[:64],
            )
            if message.audio is None:
                raise YouTubeError("Telegram не подтвердил файл")
            return message.audio.file_id, message.audio.file_unique_id
        async with download_lock:
            try:
                ingest=YouTubeIngestService(db, publish)
                result=await ingest.import_video(video_id)
                if result["reused"]:
                    track=db.get_track(result["track_id"])
                    if track:
                        await bot.send_audio(owner_id,track.file_id)
                else:
                    await bot.send_message(
                        owner_id,
                        "🎧 Аудио сохранено в Telegram. Оно доступно в /wave.",
                    )
            except (YouTubeError, TelegramAPIError):
                await bot.send_message(
                    owner_id,
                    "Не удалось получить аудио. Проверь права на файл, "
                    "поддержку YouTube, yt-dlp и FFmpeg на сервере.",
                )

    @router.message(Command("ytget"))
    async def get(message: Message, bot: Bot):
        pieces=(message.text or "").split()
        if len(pieces) != 2:
            await message.answer("Пример: /ytget VIDEO_ID из результатов /yt")
            return
        try:
            validate_video_id(pieces[1])
        except YouTubeError:
            await message.answer("Некорректный YouTube ID")
            return
        await fetch(bot,pieces[1],message.chat.id)

    @router.callback_query(F.data.startswith("ytget:"))
    async def download_button(callback: CallbackQuery, bot: Bot):
        raw=(callback.data or "").partition(":")[2]
        try:
            validate_video_id(raw)
        except YouTubeError:
            await callback.answer("Неверный YouTube ID",show_alert=True)
            return
        await callback.answer("Запрос на загрузку принят")
        await fetch(bot,raw,owner_id)
