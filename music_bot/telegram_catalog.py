"""Optional Telegram chat commands for outside-library music discovery.

Works without the Mini App or a publicly configured HTTPS web endpoint.
Search results are metadata: no Telegram audio file exists until imported.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from music_bot.catalog import CatalogError, CatalogTrack, MusicCatalog, normalize, valid_mbid
from music_bot.storage import Library


def catalog_keyboard(rows: list[CatalogTrack]) -> InlineKeyboardMarkup:
    """A fixed action set, no external content in callback_data."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"📌 {track.artist[:14]} — {track.title[:25]}",
            callback_data=f"wish:{track.mbid}",
        )]
        for track in rows[:8]
    ])


def default_artist(db: Library) -> str | None:
    favorites = db.playlist_tracks(db.liked_id(), 1000)
    ratings = db.ratings()
    pool = favorites or sorted(db.all_tracks(1000),
                               key=lambda t: ratings.get(t.id, 0), reverse=True)
    return next((
        track.artist for track in pool
        if track.artist.strip() and normalize(track.artist) != "неизвестный исполнитель"
    ), None)


def format_catalog(rows: list[CatalogTrack], title: str) -> str:
    lines = [title, "Новые находки — метаданные, не Telegram-аудио."]
    for i, item in enumerate(rows[:8], 1):
        variant = " [другая версия]" if item.variant else ""
        lines.append(f"{i}. {item.artist} — {item.title}{variant}"[:250])
    lines.append("📌 Отложить — добавить в список ожидания. Для прослушивания нужен файл.")
    return "\n".join(lines)[:3800]


def install_catalog_handlers(router: Router, db: Library, catalog: MusicCatalog) -> None:
    """Handlers inherit the parent Router's owner-only middleware."""

    @router.message(Command("find"))
    async def find(message: Message):
        query = (message.text or "").partition(" ")[2].strip()
        if not 2 <= len(query) <= 100:
            await message.answer("Пример: /find Portishead Roads")
            return
        try:
            rows = await catalog.search(query, limit=8, allow_variants=False)
        except (CatalogError, ValueError) as exc:
            await message.answer(f"Поиск недоступен: {str(exc)[:160]}")
            return
        if not rows:
            await message.answer("Не нашёл подходящих записей в MusicBrainz.")
            return
        db.remember_candidates(rows)
        await message.answer(
            format_catalog(rows, "🔎 Поиск MusicBrainz"),
            reply_markup=catalog_keyboard(rows),
        )

    @router.message(Command("radio"))
    async def radio(message: Message):
        name = (message.text or "").partition(" ")[2].strip()
        if not name:
            name = default_artist(db) or ""
        if not 2 <= len(name) <= 90:
            await message.answer(
                "Укажи исполнителя: /radio Portishead. "
                "Либо добавь понравившиеся песни для автоматического выбора."
            )
            return
        try:
            rows = await catalog.artist_radio(name, difficulty="medium", limit=8)
        except (CatalogError, ValueError) as exc:
            await message.answer(f"Радио недоступно: {str(exc)[:160]}")
            return
        if not rows:
            await message.answer(
                "ListenBrainz пока не нашёл подходящих записей. "
                "Попробуй другого исполнителя."
            )
            return
        db.remember_candidates(rows)
        await message.answer(
            format_catalog(rows, f"🎸 Похожие на {name}"),
            reply_markup=catalog_keyboard(rows),
        )

    @router.message(Command("saved"))
    async def saved(message: Message):
        entries = db.wishlist(20)
        if not entries:
            await message.answer("📌 Список отложенной музыки пуст. Используй /find.")
            return
        text = "📌 Отложенная музыка — только каталог:\n"
        text += "\n".join(
            f"{index}. {entry['artist']} — {entry['title']}"[:150]
            for index, entry in enumerate(entries, 1)
        )
        controls = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"❌ {entry['title'][:24]}",
                callback_data=f"unwish:{entry['mbid']}",
            )] for entry in entries[:8]
        ])
        await message.answer(text[:3800], reply_markup=controls)

    @router.callback_query(F.data.startswith("wish:"))
    async def wish(callback: CallbackQuery):
        mbid = (callback.data or "").partition(":")[2].lower()
        if not valid_mbid(mbid):
            await callback.answer("Некорректная запись", show_alert=True)
            return
        try:
            created = db.wishlist_add(mbid)
        except ValueError:
            await callback.answer("Запись не найдена в каталоге", show_alert=True)
            return
        await callback.answer("Отложено 📌" if created else "Уже отложено")

    @router.callback_query(F.data.startswith("unwish:"))
    async def unwish(callback: CallbackQuery):
        mbid = (callback.data or "").partition(":")[2].lower()
        if not valid_mbid(mbid):
            await callback.answer("Некорректная запись", show_alert=True)
            return
        deleted = db.wishlist_remove(mbid)
        await callback.answer("Убрано из отложенного" if deleted else "Уже убрано")
