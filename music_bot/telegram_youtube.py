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

from music_bot.catalog import CatalogError, MusicCatalog, valid_mbid
from music_bot.personalized import personalized_discovery
from music_bot.verdicts import LABELS, yandex_search_url
from music_bot.source_matching import suggest_sources
from music_bot.storage import Library
from music_bot.youtube import (
    YouTubeError, YouTubeIngestService, YouTubeSource, validate_video_id,
)


def youtube_keyboard(videos, download_enabled: bool,
                     recording_mbid: str | None = None):
    if not download_enabled:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🎧 Скачать: {video.title[:38]}",
            callback_data=(
                f"ytget:{video.video_id}:{recording_mbid}"
                if recording_mbid else f"ytget:{video.video_id}"
            ),
        )] for video in videos[:8]
    ])


def discovery_next_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Следующий трек →", callback_data="nav:discover"),
    ]])


def audio_reaction_keyboard(track_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=LABELS[status],
                              callback_data=f"vset:l:{track_id}:{status}")
         for status in ("add", "maybe")],
        [InlineKeyboardButton(text=LABELS[status],
                              callback_data=f"vset:l:{track_id}:{status}")
         for status in ("later", "never")],
        [InlineKeyboardButton(text=LABELS["interesting"],
                              callback_data=f"vset:l:{track_id}:interesting")],
        *discovery_next_keyboard().inline_keyboard,
    ])


async def offer_discovery_audio(bot: Bot, db: Library, chat_id: int,
                                catalog: MusicCatalog,
                                source: YouTubeSource | None = None) -> None:
    """Find one new recording and its likely source without manual queries.

    Never assume that a search result is licensed or that metadata similarity
    confirms identity. One explicit source choice authorizes the existing
    owner-only import flow; the next recommendation needs no typed command.
    """
    lease = db.acquire_media_import("taste", "audio-discovery", ttl=180)
    if lease is None:
        await bot.send_message(chat_id, "Уже ищу музыку для тебя.")
        return
    try:
        await bot.send_message(chat_id, "🎧 Ищу следующую песню по твоему вкусу…")
        try:
            _, rows = await personalized_discovery(db, catalog, limit=1)
        except CatalogError:
            await bot.send_message(chat_id, "Музыкальный каталог временно недоступен.",
                                   reply_markup=discovery_next_keyboard())
            return
        if not rows:
            await bot.send_message(chat_id, "Пока нет новых рекомендаций.",
                                   reply_markup=discovery_next_keyboard())
            return
        track = rows[0]
        db.remember_candidates([track])
        worker = source or YouTubeSource()
        try:
            videos = await worker.search(f"{track.artist} {track.title}"[:90], limit=6)
        except (YouTubeError, ValueError):
            videos = []
        db.remember_youtube_videos(videos)
        ranked = suggest_sources(db.get_candidate(track.mbid), videos, limit=3)
        # Show plausible sources, not arbitrary search results or known variants.
        plausible = [item for item in ranked if item.score >= 0.80
                     and "Возможно ремикс, кавер или live" not in item.warnings
                     and "Не совпал исполнитель" not in item.warnings
                     and "Длительность заметно отличается" not in item.warnings]
        controls = []
        lines = [f"🎵 {track.artist} — {track.title}"]
        for i, item in enumerate(plausible, 1):
            lines.append(f"{i}. {item.video.title} · {item.video.channel}"[:240])
            controls.append([InlineKeyboardButton(
                text=f"🎧 Получить аудио {i} (есть разрешение)",
                callback_data=f"ytget:{item.video.video_id}:{track.mbid}",
            )])
        if plausible:
            lines.append("Выбери верную версию, если её загрузка разрешена источником и правообладателем.")
        else:
            lines.append("Не нашёл надёжного варианта аудио. Можно пропустить трек.")
        controls.append([InlineKeyboardButton(text="Яндекс Музыка ↗",
                                               url=yandex_search_url(track.artist, track.title))])
        controls.extend(discovery_next_keyboard().inline_keyboard)
        await bot.send_message(chat_id, "\n".join(lines)[:3800],
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=controls))
    finally:
        db.release_media_import("taste", "audio-discovery", lease)


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

    @router.message(Command("ytfor"))
    async def youtube_for_recording(message: Message):
        if not search_enabled:
            await message.answer("YouTube-поиск выключен в .env.")
            return
        parts=(message.text or "").split()
        if len(parts)!=2 or not valid_mbid(parts[1]):
            await message.answer("Пример: /ytfor MBID найденной записи MusicBrainz")
            return
        recording=db.get_candidate(parts[1].lower())
        if recording is None:
            await message.answer("Сначала найди запись через /find.")
            return
        try:
            found=await worker.search(
                f"{recording['artist']} {recording['title']}"[:90],limit=8,
            )
        except (YouTubeError,ValueError):
            await message.answer("YouTube не дал результатов для этой песни.")
            return
        db.remember_youtube_videos(found)
        ranked=suggest_sources(recording,found,limit=6)
        if not ranked:
            await message.answer("Надёжных вариантов не нашлось. Попробуй /yt.")
            return
        text=f"▶ Вероятные версии: {recording['artist']} — {recording['title']}\n"
        text+="\n".join(
            f"{i}. {item.video.title} ({round(item.score*100)}% совпадение)"
            + (f" — {', '.join(item.warnings)}" if item.warnings else "")
            for i,item in enumerate(ranked,1)
        )
        text+="\nВыбери подходящую запись; автоимпорт не выполняется."
        await message.answer(
            text[:3800],
            reply_markup=youtube_keyboard(
                [item.video for item in ranked],
                download_enabled, recording["mbid"],
            ),
        )

    async def fetch(bot: Bot, video_id: str, chat_id: int,
                    recording_mbid: str | None = None):
        if not download_enabled:
            await bot.send_message(chat_id, "Загрузка YouTube отключена в .env.")
            return
        sent_message = None
        async def publish(path, title, channel):
            nonlocal sent_message
            message = await bot.send_audio(
                chat_id=owner_id, audio=FSInputFile(path),
                title=title[:64], performer=channel[:64],
            )
            if message.audio is None:
                raise YouTubeError("Telegram не подтвердил файл")
            sent_message = message
            return message.audio.file_id, message.audio.file_unique_id
        async with download_lock:
            try:
                ingest=YouTubeIngestService(db, publish)
                result=await ingest.import_video(
                    video_id, recording_mbid=recording_mbid,
                )
                track = db.get_track(result["track_id"])
                if track is None:
                    raise YouTubeError("Сохранённый файл не найден")
                markup = audio_reaction_keyboard(track.id)
                if result["reused"]:
                    await bot.send_audio(owner_id, track.file_id,
                                         title=track.title[:64], performer=track.artist[:64],
                                         reply_markup=markup)
                else:
                    try:
                        await sent_message.edit_reply_markup(reply_markup=markup)
                    except TelegramAPIError:
                        await bot.send_message(owner_id, "🎧 Аудио сохранено. Оцени трек:",
                                               reply_markup=markup)
                db.record_single_send(track.id)
            except (YouTubeError, TelegramAPIError):
                await bot.send_message(
                    owner_id,
                    "Не удалось получить аудио. Проверь права на файл, "
                    "поддержку YouTube, yt-dlp и FFmpeg на сервере.",
                    reply_markup=discovery_next_keyboard(),
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
        parts = (callback.data or "").split(":")
        if (len(parts) not in (2, 3) or parts[0] != "ytget"):
            await callback.answer("Неверная кнопка", show_alert=True)
            return
        raw = parts[1]
        mbid = parts[2].lower() if len(parts) == 3 else None
        try:
            validate_video_id(raw)
            if mbid is not None and not valid_mbid(mbid):
                raise YouTubeError("Bad MBID")
        except YouTubeError:
            await callback.answer("Неверный YouTube/MusicBrainz ID",
                                  show_alert=True)
            return
        await callback.answer("Запрос на загрузку принят")
        await fetch(bot, raw, owner_id, recording_mbid=mbid)
