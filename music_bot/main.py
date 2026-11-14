"""Private polling Telegram bot."""
import asyncio
import logging
import sqlite3

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaAudio

from music_bot.config import load_settings
from music_bot.storage import Library
from music_bot.recommend import wave

HELP = """🎵 music
Пришли аудиофайл для добавления в библиотеку.
/library — все треки
/liked — понравившиеся
/like ID /dislike ID — оценка
/playliked — слушать понравившиеся
/newplaylist Название — создать
/playlists — список
/addto ПЛЕЙЛИСТ_ID ТРЕК_ID — добавить
/playplaylist ID — слушать
/wave — твоя Волна"""

class OwnerOnly(BaseMiddleware):
    def __init__(self, owner_id):
        self.owner_id = owner_id

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        chat = data.get("event_chat")
        if user is None or user.id != self.owner_id:
            return None
        if chat is None or chat.type != "private":
            return None
        return await handler(event, data)

def arguments(message, n):
    p = (message.text or "").split()
    if len(p) != n + 1:
        return None
    try:
        nums = tuple(int(v) for v in p[1:])
    except ValueError:
        return None
    return nums if all(v > 0 for v in nums) else None

async def play(message, bot, tracks):
    if not tracks:
        await message.answer("Плейлист пуст.")
    elif len(tracks) == 1:
        t = tracks[0]
        await bot.send_audio(message.chat.id, audio=t.file_id,
                             caption=f"#{t.id} {t.artist} — {t.title}")
    else:
        await bot.send_media_group(
            message.chat.id,
            media=[InputMediaAudio(media=t.file_id, caption=f"#{t.id} {t.artist} — {t.title}")
                   for t in tracks[:10]])

def create_router(db, owner_id):
    router = Router()
    router.message.middleware(OwnerOnly(owner_id))
    router.callback_query.middleware(OwnerOnly(owner_id))

    @router.message(Command("start", "help"))
    async def start(message):
        await message.answer(HELP)

    @router.message(F.audio)
    async def audio(message):
        a = message.audio
        t = db.add_track(a.file_id, a.file_unique_id,
                         a.title or a.file_name or "Без названия",
                         a.performer or "Неизвестный исполнитель")
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❤️ Нравится", callback_data=f"like:{t.id}")]])
        await message.answer(f"Сохранено #{t.id}: {t.artist} — {t.title}", reply_markup=kb)

    @router.callback_query(F.data.startswith("like:"))
    async def like_button(callback):
        try:
            db.like(int((callback.data or "").split(":", 1)[1]))
        except (ValueError, IndexError):
            await callback.answer("Ошибка", show_alert=True)
            return
        await callback.answer("Добавлено ❤️")

    @router.message(Command("library"))
    async def library(message):
        ts = db.all_tracks(20)
        await message.answer("\n".join(f"#{t.id} {t.artist} — {t.title}" for t in ts)
                             if ts else "Библиотека пуста. Пришли аудио.")

    @router.message(Command("liked"))
    async def liked(message):
        ts = db.playlist_tracks(db.liked_id(), 50)
        await message.answer("\n".join(f"#{t.id} {t.artist} — {t.title}" for t in ts)
                             if ts else "Пока ничего не понравилось.")

    @router.message(Command("like"))
    async def like(message):
        ids = arguments(message, 1)
        if not ids:
            await message.answer("Пример: /like 12")
            return
        try:
            added = db.like(ids[0])
            await message.answer("❤️ Добавлено" if added else "Уже в понравившихся")
        except ValueError as exc:
            await message.answer(str(exc))

    @router.message(Command("dislike"))
    async def dislike(message):
        ids = arguments(message, 1)
        if not ids:
            await message.answer("Пример: /dislike 12")
            return
        try:
            db.dislike(ids[0])
            await message.answer("Исключено из Волны.")
        except ValueError as exc:
            await message.answer(str(exc))

    @router.message(Command("playliked"))
    async def playliked(message, bot):
        await play(message, bot, db.playlist_tracks(db.liked_id(), 10))

    @router.message(Command("newplaylist"))
    async def new_playlist(message):
        name = (message.text or "").partition(" ")[2].strip()
        try:
            pid = db.create_playlist(name)
        except (ValueError, sqlite3.IntegrityError):
            await message.answer("Название пустое, слишком длинное или уже занято.")
            return
        await message.answer(f"Создан плейлист #{pid}: {name}")

    @router.message(Command("playlists"))
    async def playlists(message):
        await message.answer("\n".join(
            f"#{p['id']} {p['name']} ({p['count']})" for p in db.playlists()))

    @router.message(Command("addto"))
    async def addto(message):
        ids = arguments(message, 2)
        if not ids:
            await message.answer("Пример: /addto 2 12")
            return
        try:
            added = db.add_to(*ids)
            await message.answer("Добавлено" if added else "Уже есть")
        except ValueError as exc:
            await message.answer(str(exc))

    @router.message(Command("playplaylist"))
    async def playplaylist(message, bot):
        ids = arguments(message, 1)
        if not ids:
            await message.answer("Пример: /playplaylist 2")
            return
        await play(message, bot, db.playlist_tracks(ids[0], 10))

    @router.message(Command("wave"))
    async def wave_cmd(message, bot):
        ts = wave(db.all_tracks(), db.liked_ids(), db.disliked_ids())
        await play(message, bot, ts)

    return router

async def main():
    logging.basicConfig(level=logging.INFO)
    cfg = load_settings()
    db = Library(cfg.db_path)
    bot = Bot(token=cfg.token)
    dp = Dispatcher()
    dp.include_router(create_router(db, cfg.owner_id))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
