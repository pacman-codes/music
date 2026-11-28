"""Personal album shelf in Telegram, using the existing release-group opener."""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from music_bot.catalog import CatalogError
from music_bot.personal_albums import recommend_albums


def install_personal_album_handlers(router: Router, db, catalog):
    async def show(message: Message, page=0):
        if catalog is None:
            await message.answer("Каталог альбомов отключён.")
            return
        try:
            data = await recommend_albums(db, catalog, page=page)
        except CatalogError as exc:
            await message.answer(str(exc))
            return
        lines = ["💿 Альбомы для тебя", f"Подборка {page+1}"]
        if data["seed_artists"]:
            lines.append("На основе: " + ", ".join(data["seed_artists"]))
        rows = []
        for album in data["results"]:
            why = "Из любимых исполнителей" if album["reason"] == "preferred" else "Похоже на " + album["seed"]
            lines.append(f"{album['artist'][:65]} — {album['title'][:85]} ({album['date'][:4] or '—'})\n{why[:100]}")
            rows.append([InlineKeyboardButton(text=f"💿 {album['title'][:42]}",
                         callback_data=f"artistgroup:{album['mbid']}")])
        if not data["results"]:
            lines.append("Пока нет подходящих альбомов. Добавь любимых артистов или оцени песни.")
        lines.extend(data["warnings"])
        nav = []
        if page:
            nav.append(InlineKeyboardButton(text="←", callback_data=f"personalalbums:{page-1}"))
        if data["has_more"]:
            nav.append(InlineKeyboardButton(text="Другая подборка →", callback_data=f"personalalbums:{page+1}"))
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton(text="↻ Обновить", callback_data=f"personalalbums:{page}"),
                     InlineKeyboardButton(text="💿 Мои альбомы", callback_data="albumlist:0")])
        await message.answer("\n".join(lines)[:3800], reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @router.message(Command("albumforyou"))
    async def command(message: Message):
        await show(message)

    @router.callback_query(F.data.startswith("personalalbums:"))
    async def callback_handler(callback: CallbackQuery):
        raw = (callback.data or "").partition(":")[2]
        if raw not in {"0","1","2","3","4"}:
            await callback.answer("Неверная страница", show_alert=True)
            return
        await callback.answer()
        if isinstance(callback.message, Message):
            await show(callback.message, int(raw))
