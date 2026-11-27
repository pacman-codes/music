"""Telegram smart playlist browsing and native audio queue creation."""
from __future__ import annotations

import random

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from music_bot.smart_playlists import RULES, smart_counts, smart_page, smart_tracks
from music_bot.storage import Library


def button(text, data):
    return InlineKeyboardButton(text=text, callback_data=data)


def smart_home(db: Library):
    counts = smart_counts(db)
    rows = [[button(f"{rule.title} · {counts[key]}", f"smart:{key}:0")]
            for key, rule in RULES.items()]
    rows.append([button("← Плейлисты", "nav:playlists"), button("🏠 Меню", "nav:home")])
    return "✨ Умные плейлисты\nСостав обновляется при открытии. Только аудио из медиатеки.", InlineKeyboardMarkup(inline_keyboard=rows)


def smart_detail(db: Library, key: str, page: int = 0):
    rule = RULES[key]
    total, page, pages, tracks = smart_page(db, key, page)
    lines = [f"✨ Умные плейлисты · {rule.title}", rule.description,
             f"{total} треков · {page+1}/{pages}"]
    rows = []
    if total:
        rows.append([button("▶ Слушать", f"smartplay:{key}:all"),
                     button("🔀 Перемешать", f"smartplay:{key}:shuffle")])
    else:
        lines.append("Пока нет подходящих песен.")
    for i, track in enumerate(tracks, page*8+1):
        lines.append(f"{i}. {track.artist[:45]} — {track.title[:90]}")
        rows.append([button(f"▶ {track.title[:36]}", f"play:{track.id}"),
                     button("⋯", f"vopen:l:{track.id}")])
    nav = []
    if page:
        nav.append(button("←", f"smart:{key}:{page-1}"))
    if page+1 < pages:
        nav.append(button("→", f"smart:{key}:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([button("↻ Обновить", f"smart:{key}:{page}"),
                 button("← Все подборки", "smart:home")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def create_smart_queue(db: Library, key: str, mode: str = "all") -> int:
    if key not in RULES or mode not in {"all", "shuffle"}:
        raise ValueError("Invalid smart queue")
    ids = [t.id for t in smart_tracks(db, key)]
    if not ids:
        raise ValueError("Пока нет подходящих песен")
    if len(ids) > 1000:
        raise ValueError("Больше 1000 песен. Пока можно запускать отдельные треки.")
    if mode == "shuffle":
        random.shuffle(ids)
    return db.create_queue(ids, RULES[key].title + (" · случайный порядок" if mode == "shuffle" else ""), "playlist")


def install_smart_handlers(router: Router, db: Library, send_queue, render_panel):
    @router.message(Command("smart"))
    async def smart_command(message: Message):
        text, markup = smart_home(db)
        await render_panel(message, text, markup)

    @router.callback_query(F.data.startswith("smart:"))
    async def smart_open(callback: CallbackQuery):
        parts = (callback.data or "").split(":")
        if parts == ["smart", "home"]:
            text, markup = smart_home(db)
        elif (len(parts) == 3 and parts[1] in RULES and parts[2].isascii()
              and parts[2].isdigit() and len(parts[2]) <= 6 and int(parts[2]) <= 100000):
            text, markup = smart_detail(db, parts[1], int(parts[2]))
        else:
            await callback.answer("Неверная подборка", show_alert=True)
            return
        await callback.answer()
        if isinstance(callback.message, Message):
            await render_panel(callback.message, text, markup, edit=True)

    @router.callback_query(F.data.startswith("smartplay:"))
    async def smart_play(callback: CallbackQuery, bot: Bot):
        parts = (callback.data or "").split(":")
        if len(parts) != 3 or parts[1] not in RULES or parts[2] not in {"all", "shuffle"}:
            await callback.answer("Неверная подборка", show_alert=True)
            return
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        try:
            qid = create_smart_queue(db, parts[1], parts[2])
        except ValueError as exc:
            await callback.answer(str(exc)[:180], show_alert=True)
            return
        await callback.answer()
        await send_queue(bot, callback.message.chat.id, qid)
