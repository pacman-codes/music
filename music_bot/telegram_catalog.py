"""Optional Telegram chat commands for outside-library music discovery.

Works without the Mini App or a publicly configured HTTPS web endpoint.
Search results are metadata: no Telegram audio file exists until imported.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from music_bot.catalog import CatalogError, CatalogTrack, MusicCatalog, normalize, valid_mbid
from music_bot.personalized import personalized_discovery
from music_bot.weekly import weekly_discovery
from music_bot.storage import Library


def catalog_keyboard(rows: list[CatalogTrack], *,
                     match_track_id: int | None = None,
                     linked: dict[str, int] | None = None
                     ) -> InlineKeyboardMarkup:
    """A fixed action set, no external content in callback_data."""
    controls = []
    linked = linked or {}
    for track in rows[:8]:
        playable_id = linked.get(track.mbid)
        if playable_id is not None:
            controls.append([InlineKeyboardButton(
                text=f"▶ Слушать: {track.title[:30]}",
                callback_data=f"play:{playable_id}",
            )])
        if match_track_id is not None and playable_id is None:
            controls.append([InlineKeyboardButton(
                text=f"🔗 Подтвердить: {track.title[:35]}",
                callback_data=f"match:{match_track_id}:{track.mbid}",
            )])
        controls.append([InlineKeyboardButton(
            text=f"📌 {track.artist[:14]} — {track.title[:25]}",
            callback_data=f"wish:{track.mbid}",
        )])
    return InlineKeyboardMarkup(inline_keyboard=controls)


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
    lines = [title,
             "MusicBrainz показывает записи каталога. ▶ доступно только для "
             "уже загруженного и подтверждённого Telegram-аудио."]
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
            reply_markup=catalog_keyboard(rows, linked=db.resolved_catalog_tracks()),
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
            reply_markup=catalog_keyboard(rows, linked=db.resolved_catalog_tracks()),
        )

    @router.message(Command("identify"))
    async def identify(message: Message):
        raw = (message.text or "").split()
        if len(raw) != 2 or not raw[1].isdigit() or int(raw[1]) <= 0:
            await message.answer("Пример: /identify 12 — найти MusicBrainz-запись")
            return
        track = db.get_track(int(raw[1]))
        if track is None:
            await message.answer("Такого трека нет в библиотеке.")
            return
        existing = db.recording_for_track(track.id)
        if existing is not None:
            await message.answer(
                f"#{track.id} связан с MusicBrainz: {existing['mbid']} "
                f"({existing['provenance']}). Для ручного совпадения — "
                f"/unmatch {track.id}."
            )
            return
        try:
            rows = await catalog.search(
                f"{track.artist} {track.title}"[:100],
                limit=8, allow_variants=True,
            )
        except (CatalogError, ValueError):
            await message.answer("MusicBrainz пока не отвечает.")
            return
        if not rows:
            await message.answer("Не нашёл подходящих записей. Попробуй /find.")
            return
        db.remember_candidates(rows)
        await message.answer(
            format_catalog(rows, f"🔗 Определи запись для #{track.id}: "
                                 f"{track.artist} — {track.title}") +
            "\nПодтверди только точное совпадение: live/remix могут отличаться.",
            reply_markup=catalog_keyboard(
                rows, match_track_id=track.id,
                linked=db.resolved_catalog_tracks(),
            ),
        )

    @router.message(Command("unmatch"))
    async def unmatch(message: Message):
        raw = (message.text or "").split()
        if len(raw) != 2 or not raw[1].isdigit() or int(raw[1]) <= 0:
            await message.answer("Пример: /unmatch 12")
            return
        changed = db.unlink_recording_match(int(raw[1]))
        await message.answer(
            "Ручная привязка убрана." if changed else
            "Нет ручной привязки. Связь после импорта не удаляется этой командой."
        )

    @router.message(Command("radiotrack"))
    async def radiotrack(message: Message):
        raw = (message.text or "").split()
        if len(raw) != 2 or not raw[1].isdigit() or int(raw[1]) <= 0:
            await message.answer("Пример: /radiotrack 12")
            return
        track = db.get_track(int(raw[1]))
        if track is None:
            await message.answer("Трек не найден.")
            return
        linked = db.recording_for_track(track.id)
        artist_id = linked.get("artist_mbid") if linked else None
        try:
            if artist_id and valid_mbid(artist_id):
                rows = await catalog.artist_radio_by_id(
                    artist_id, difficulty="medium", limit=10,
                )
            else:
                rows = await catalog.artist_radio(
                    track.artist, difficulty="medium", limit=10,
                )
        except (CatalogError, ValueError):
            await message.answer("Радио по треку сейчас недоступно.")
            return
        local = db.confirmed_recording_ids()
        local_names = {
            (normalize(t.artist), normalize(t.title))
            for t in db.all_tracks(1000)
        }
        fresh = [r for r in rows if r.mbid not in local and
                 (normalize(r.artist), normalize(r.title)) not in local_names]
        if not fresh:
            await message.answer("Новых записей по этому треку пока не нашлось.")
            return
        db.remember_candidates(fresh)
        await message.answer(
            format_catalog(fresh, f"🎸 Радио по #{track.id}: {track.title}"),
            reply_markup=catalog_keyboard(fresh, linked=db.resolved_catalog_tracks()),
        )

    @router.callback_query(F.data.startswith("match:"))
    async def match_button(callback: CallbackQuery):
        parts = (callback.data or "").split(":")
        if len(parts) != 3 or not parts[1].isdigit() or int(parts[1]) <= 0:
            await callback.answer("Неверный трек", show_alert=True)
            return
        tid = int(parts[1])
        mbid = parts[2].lower()
        if not valid_mbid(mbid):
            await callback.answer("Неверная запись", show_alert=True)
            return
        try:
            record = db.confirm_recording_match(tid, mbid)
        except ValueError as exc:
            await callback.answer(str(exc)[:140], show_alert=True)
            return
        await callback.answer(
            f"Связано с {record['artist']} — {record['title']}"[:180],
            show_alert=True,
        )

    @router.message(Command("weekly"))
    async def weekly(message: Message):
        result = await weekly_discovery(db, catalog, limit=8)
        rows = [
            CatalogTrack(t["mbid"], t["title"], t["artist"],
                         t.get("artist_mbid"), t.get("duration_ms"),
                         t.get("variant", False))
            for t in result["tracks"]
        ]
        db.remember_candidates(rows)
        lines = [f"✨ Находки недели {result['year']} / {result['week']}"]
        if result["seed_artists"]:
            lines.append("По твоему вкусу: "+", ".join(result["seed_artists"]))
        if result["releases"]:
            lines.append("💿 Новые релизы (только метаданные):")
            lines.extend(
                f"• {item['artist']} — {item['name']} ({item['release_date']})"
                for item in result["releases"][:8]
            )
        if rows:
            lines.append("🎧 Персональные песни (пока без файла):")
            lines.extend(
                f"• {t.artist} — {t.title} [{t.mbid}]"
                for t in rows[:8]
            )
        if not result["releases"] and not rows:
            lines.append("Пока нет новых находок. Добавь оценки и избранное.")
        if result["warnings"]:
            lines.append("Некоторые музыкальные источники сейчас не отвечают.")
        await message.answer(
            "\n".join(lines)[:3900],
            reply_markup=(catalog_keyboard(
                rows, linked=db.resolved_catalog_tracks(),
            ) if rows else None),
        )

    @router.message(Command("recommend"))
    async def recommend(message: Message):
        try:
            seeds, rows = await personalized_discovery(
                db, catalog, difficulty="medium", limit=8,
            )
        except CatalogError:
            await message.answer("Персональный поиск пока недоступен.")
            return
        if not seeds:
            await message.answer(
                "Добавь песни в «Мне нравится» или поставь им оценку от 7 до 10. "
                "Потом используй /recommend."
            )
            return
        if not rows:
            await message.answer(
                "По твоим любимым исполнителям пока не нашлось новых записей. "
                "Попробуй /radio с другим исполнителем."
            )
            return
        db.remember_candidates(rows)
        await message.answer(
            format_catalog(rows, "✨ Твои открытия по: " + ", ".join(seeds)),
            reply_markup=catalog_keyboard(rows, linked=db.resolved_catalog_tracks()),
        )

    @router.message(Command("saved"))
    async def saved(message: Message):
        entries = db.wishlist(20)
        if not entries:
            await message.answer("📌 Список отложенной музыки пуст. Используй /find.")
            return
        text = "📌 Отложенная музыка (▶ если аудио уже загружено):\n"
        text += "\n".join(
            f"{index}. {entry['artist']} — {entry['title']}"[:150]
            for index, entry in enumerate(entries, 1)
        )
        linked = db.resolved_catalog_tracks()
        buttons = []
        for entry in entries[:8]:
            track_id = linked.get(entry["mbid"])
            if track_id is not None:
                buttons.append([InlineKeyboardButton(
                    text=f"▶ {entry['title'][:27]}",
                    callback_data=f"play:{track_id}",
                )])
            buttons.append([InlineKeyboardButton(
                text=f"❌ {entry['title'][:24]}",
                callback_data=f"unwish:{entry['mbid']}",
            )])
        controls = InlineKeyboardMarkup(inline_keyboard=buttons)
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
