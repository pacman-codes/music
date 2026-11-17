"""Owner-only Telegram commands for external liked libraries.

No Google/Yandex cookies, no audio downloads, no implicit track plays.
"""
from __future__ import annotations

import io
from urllib.parse import quote

from aiogram import Bot, Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from music_bot.storage import Library
from music_bot.taste_sources import (
    TasteSourceError, parse_yandex_url, fetch_yandex_playlist,
    parse_youtube_export, MAX_SOURCE_BYTES,
)

LABELS = {"yandex": "🎵 Яндекс Музыка", "youtube_music": "▶ YouTube Music"}


def install_taste_handlers(router: Router, db: Library) -> None:
    """The parent Router enforces owner and private Telegram chat."""
    async def import_yandex(message: Message, url: str) -> None:
        try:
            parse_yandex_url(url)
            lease = db.acquire_media_import("taste", "yandex", ttl=150)
            if lease is None:
                await message.answer("Синхронизация Яндекс Музыки уже идёт.")
                return
            try:
                songs = await fetch_yandex_playlist(url)
                result = db.sync_taste_source("yandex", songs, public_url=url, complete=True)
            finally:
                db.release_media_import("taste", "yandex", lease)
        except (ValueError, TasteSourceError) as exc:
            await message.answer(f"Не удалось обновить Яндекс Музыку: {exc}")
            return
        await message.answer(
            f"🎵 Яндекс Музыка: {result['active']} любимых треков; "
            f"новых +{result['added']}, исчезло из плейлиста {result['removed']}. "
            "Это метаданные для рекомендаций, не загруженное Telegram-аудио."
        )

    async def import_youtube_document(message: Message, bot: Bot) -> None:
        document = message.document or (
            message.reply_to_message.document if message.reply_to_message else None
        )
        if document is None:
            await message.answer(
                "Пришли CSV/TXT с подписью /ytmimport либо ответь "
                "/ytmimport на сообщение с документом. "
                "Для CSV используй Google Takeout → YouTube Music → Music library songs."
            )
            return
        if (document.file_size is None or document.file_size > MAX_SOURCE_BYTES
                or document.file_size <= 0):
            await message.answer("Файл должен быть не больше 24 МиБ.")
            return
        name = document.file_name or ""
        if not name.lower().endswith((".csv", ".txt")):
            await message.answer("Поддерживаются только CSV/TXT, не ZIP.")
            return
        lease = db.acquire_media_import("taste", "youtube_music", ttl=180)
        if lease is None:
            await message.answer("Импорт YouTube Music уже выполняется.")
            return
        try:
            file = await bot.get_file(document.file_id)
            if not file.file_path:
                raise TasteSourceError("Telegram не выдал путь к документу")
            target = io.BytesIO()
            await bot.download_file(file.file_path, destination=target, timeout=90)
            if len(target.getvalue()) > MAX_SOURCE_BYTES:
                raise TasteSourceError("Загруженный файл превышает лимит")
            songs, complete = parse_youtube_export(target.getvalue(), name)
            result = db.sync_taste_source(
                "youtube_music", songs, complete=complete,
            )
        except (TasteSourceError, ValueError) as exc:
            await message.answer(f"Не удалось импортировать YouTube Music: {exc}")
            return
        except Exception:
            await message.answer("Telegram не смог передать файл, попробуй ещё раз.")
            return
        finally:
            db.release_media_import("taste", "youtube_music", lease)
        note = (
            "Полный CSV: отсутствующие записи удалены из активных лайков."
            if result["complete"] else
            "Текст страницы может быть неполным: существующие лайки не удалены."
        )
        await message.answer(
            f"▶ YouTube Music: {result['active']} известных любимых треков; "
            f"новых +{result['added']}. {note} "
            "Это не файлы для Telegram-плеера."
        )

    async def list_source(message: Message, provider: str, page: int = 0) -> None:
        if provider not in LABELS or not 0 <= page <= 1000:
            await message.answer("Неизвестный источник.")
            return
        sources = {s["provider"]: s for s in db.taste_sources()}
        count = sources.get(provider, {}).get("count", 0)
        page_count = max(1, (count + 7) // 8)
        page = min(page, page_count - 1)
        entries = db.taste_tracks(provider, limit=8, offset=page*8)
        lines = [f"{LABELS[provider]} — {count} записей · {page+1}/{page_count}"]
        buttons = []
        for i, item in enumerate(entries, start=page*8 + 1):
            lines.append(f"{i}. {item['artist'][:85]} — {item['title'][:110]}")
            buttons.append([InlineKeyboardButton(
                text=f"🎧 Открыть трек {i}", url=item["source_url"]
            )])
        navigation = []
        if page:
            navigation.append(InlineKeyboardButton(
                text="◀", callback_data=f"src:{provider}:{page-1}"
            ))
        if page + 1 < page_count:
            navigation.append(InlineKeyboardButton(
                text="▶", callback_data=f"src:{provider}:{page+1}"
            ))
        if navigation:
            buttons.append(navigation)
        await message.answer(
            "\n".join(lines)[:3800],
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
            if buttons else None,
        )

    @router.message(Command("ymimport"))
    async def yandex_command(message: Message):
        url = (message.text or "").partition(" ")[2].strip()
        if not url:
            await message.answer("Использование: /ymimport https://music.yandex.ru/playlists/lk.XXX")
            return
        await import_yandex(message, url)

    @router.message(Command("ymrefresh"))
    async def yandex_refresh(message: Message):
        source = next(
            (s for s in db.taste_sources() if s["provider"] == "yandex"), None
        )
        if not source or not source["public_url"]:
            await message.answer("Сначала укажи ссылку: /ymimport URL")
            return
        await import_yandex(message, source["public_url"])

    @router.message(Command("ytmimport"))
    async def youtube_command(message: Message, bot: Bot):
        await import_youtube_document(message, bot)

    @router.message(F.document)
    async def youtube_attachment(message: Message, bot: Bot):
        if (message.caption or "").split(maxsplit=1)[0:1] == ["/ytmimport"]:
            await import_youtube_document(message, bot)

    @router.message(Command("sources"))
    async def sources_command(message: Message):
        sources = db.taste_sources()
        if not sources:
            await message.answer(
                "Библиотеки не подключены.\n"
                "/ymimport URL — Яндекс по ссылке;\n"
                "/ytmimport — файл CSV/TXT из YouTube Music."
            )
            return
        labels = [
            f"{LABELS[s['provider']]} — {s['count']} записей; "
            f"синхронизация {s['last_synced_at']}"
            for s in sources
        ]
        lines = ["🎼 Внешние любимые треки:", *labels,
                 "Это сигналы вкуса для поиска новинок, не Telegram-аудиофайлы."]
        buttons = [[InlineKeyboardButton(
            text=LABELS[s["provider"]] + " · посмотреть",
            callback_data=f"src:{s['provider']}:0",
        )] for s in sources]
        await message.answer("\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

    @router.callback_query(F.data.startswith("src:"))
    async def source_page(callback: CallbackQuery):
        pieces = (callback.data or "").split(":")
        if (len(pieces) != 3 or pieces[1] not in LABELS or
                not pieces[2].isdigit() or int(pieces[2]) > 1000):
            await callback.answer("Неверная страница", show_alert=True)
            return
        await callback.answer()
        if callback.message:
            await list_source(callback.message, pieces[1], int(pieces[2]))
