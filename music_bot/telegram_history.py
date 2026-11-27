"""Owner-only delivery history and replay of previous audio batches."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from music_bot.storage import Library

PREFIX = "🎵 LAIN · История"
PAGE_SIZE = 8


def history_page(db: Library, mode: str = "tracks", before: int | None = None):
    if mode == "tracks":
        items = db.recent_deliveries(before=before, limit=PAGE_SIZE + 1)
        key = "delivery_id"
    elif mode == "queues":
        items = db.recent_delivered_queues(before=before, limit=PAGE_SIZE + 1)
        key = "last_delivery_id"
    else:
        raise ValueError("Invalid history mode")
    visible = items[:PAGE_SIZE]
    lines = [PREFIX, "Отправлено в Telegram, не факт прослушивания.",
             "Последние песни" if mode == "tracks" else "Последние подборки"]
    rows = [[InlineKeyboardButton(text="🎵 Песни", callback_data="hist:tracks:0"),
             InlineKeyboardButton(text="📂 Подборки", callback_data="hist:queues:0")]]
    for item in visible:
        if mode == "tracks":
            tid = item["track_id"]
            lines.append(f"{item['artist'][:55]} — {item['title'][:90]}")
            rows.append([InlineKeyboardButton(text=f"▶ {item['title'][:35]}", callback_data=f"play:{tid}"),
                         InlineKeyboardButton(text="⋯", callback_data=f"vopen:l:{tid}")])
        else:
            qid = item["queue_id"]
            lines.append(f"{item['label'][:100]} · {item['delivered_count']} отправлено")
            rows.append([InlineKeyboardButton(text=f"↻ {item['label'][:36]}", callback_data=f"histreplay:{qid}")])
    if not visible:
        lines.append("Пока здесь ничего нет.")
    if len(items) > PAGE_SIZE:
        rows.append([InlineKeyboardButton(text="Дальше →", callback_data=f"hist:{mode}:{visible[-1][key]}")])
    if before is not None:
        rows.append([InlineKeyboardButton(text="← К последним", callback_data=f"hist:{mode}:0")])
    rows.append([InlineKeyboardButton(text="🏠 Меню", callback_data="nav:home")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def replay_delivery_queue(db: Library, source_queue_id: int) -> int:
    tracks = db.delivered_queue_tracks(source_queue_id)
    if not tracks:
        raise ValueError("Нет отправленных песен")
    if len(tracks) > 1000:
        raise ValueError("Подборка слишком длинная для повторной отправки (лимит 1000)")
    source = db.queue_snapshot(source_queue_id, limit=1)
    return db.create_queue([track.id for track in tracks], "Повтор · " + source["label"],
                           "playlist", preserve_duplicates=True)


def install_history_handlers(router: Router, db: Library, send_queue):
    async def show(message: Message, mode="tracks", before=None, *, edit=False):
        text, markup = history_page(db, mode, before)
        if edit and not message.audio and (message.text or "").startswith(PREFIX):
            try:
                await message.edit_text(text, reply_markup=markup)
                return
            except TelegramBadRequest as exc:
                if "message is not modified" in str(exc).lower():
                    return
        await message.answer(text, reply_markup=markup)

    @router.message(Command("history", "recent"))
    async def history(message: Message):
        await show(message)

    @router.callback_query(F.data.startswith("hist:"))
    async def history_button(callback: CallbackQuery):
        try:
            _, mode, raw = (callback.data or "").split(":")
            if mode not in {"tracks", "queues"} or not raw.isascii() or not raw.isdigit() or len(raw)>18:
                raise ValueError()
            before = int(raw) or None
        except ValueError:
            await callback.answer("Неверная страница", show_alert=True)
            return
        await callback.answer()
        if isinstance(callback.message, Message):
            await show(callback.message, mode, before, edit=True)

    @router.callback_query(F.data.startswith("histreplay:"))
    async def replay(callback: CallbackQuery, bot: Bot):
        raw = (callback.data or "").partition(":")[2]
        if not raw.isascii() or not raw.isdigit() or len(raw)>18 or int(raw)<=0:
            await callback.answer("Неверная подборка", show_alert=True)
            return
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        try:
            qid = replay_delivery_queue(db, int(raw))
        except ValueError as exc:
            await callback.answer(str(exc)[:180], show_alert=True)
            return
        await callback.answer("Повторяю подборку")
        await send_queue(bot, callback.message.chat.id, qid)
