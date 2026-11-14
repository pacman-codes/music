"""Owner-only /fingerprint ID with confirmation buttons, no auto tagging."""
from __future__ import annotations

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from music_bot.fingerprints import (
    AudioRecognition, RecognitionError, recognize_telegram_track,
)
from music_bot.storage import Library


def install_fingerprint_handlers(
    router: Router, db: Library,
    recognizer: AudioRecognition | None,
) -> None:
    """Inherit OwnerOnly and private-chat enforcement from parent router."""

    @router.message(Command("fingerprint"))
    async def fingerprint_command(message: Message, bot: Bot):
        words = (message.text or "").split()
        if len(words) != 2 or not words[1].isdigit() or int(words[1]) < 1:
            await message.answer("Пример: /fingerprint 12")
            return
        if recognizer is None or not recognizer.client_key:
            await message.answer(
                "AcoustID выключен. Настрой ACOUSTID_CLIENT_KEY и fpcalc на сервере."
            )
            return
        track_id = int(words[1])
        lease = db.acquire_media_import("global", "media")
        if lease is None:
            await message.answer("Сервер занят загрузкой файла; попробуй позже.")
            return
        try:
            result = await recognize_telegram_track(
                db, bot, recognizer, track_id,
            )
        except RecognitionError as exc:
            await message.answer(f"Не удалось распознать: {str(exc)[:160]}")
            return
        finally:
            db.release_media_import("global", "media", lease)
        summary = (
            f"🧬 Отпечаток #{track_id}, {result['duration_sec']} сек. "
            f"Возможные совпадения: {len(result['results'])}."
        )
        if result["duplicates"]:
            summary += (
                "\nПохожие отпечатки у треков: "
                + ", ".join("#"+str(v) for v in result["duplicates"])
            )
        buttons = []
        for record in result["results"][:6]:
            summary += (
                f"\n• {record['confidence']:.0%} · "
                f"{record['artist']} — {record['title']}"
            )[:190]
            buttons.append([InlineKeyboardButton(
                text=f"🔗 Подтвердить {record['title'][:24]}",
                callback_data=f"match:{track_id}:{record['mbid']}",
            )])
        summary += "\nНи одно совпадение не подтверждается автоматически."
        await message.answer(
            summary[:3800],
            reply_markup=(InlineKeyboardMarkup(inline_keyboard=buttons)
                          if buttons else None),
        )
