"""Owner-only artist collection, local audio, discography and artist radio."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Message

from music_bot.artists import artist_by_id, artist_discography, group_album, search_artists
from music_bot.catalog import CatalogError, normalize, valid_mbid
from music_bot.storage import Library
from music_bot.verdicts import yandex_search_url


def b(text, data):
    return InlineKeyboardButton(text=text, callback_data=data)


def markup(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def artist_home(db: Library, page=0):
    artists = db.saved_artists(offset=page*8, limit=9)
    lines = ["👤 Исполнители · Избранное", "Поиск: /artist Имя исполнителя"]
    rows = []
    for artist in artists[:8]:
        label = artist["name"] + (" · " + artist["disambiguation"] if artist["disambiguation"] else "")
        lines.append(label[:180])
        rows.append([b(label[:55], f"artistopen:{artist['mbid']}:0")])
    if not artists:
        lines.append("Здесь пока пусто.")
    nav = []
    if page:
        nav.append(b("←", f"artistlist:{page-1}"))
    if len(artists)>8:
        nav.append(b("→", f"artistlist:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([b("🔎 Найти исполнителя", "artistsearch"), b("🏠 Меню", "nav:home")])
    return "\n".join(lines), markup(rows)


def artist_page(db: Library, mbid: str, page=0):
    artist = db.get_artist(mbid)
    if artist is None:
        raise ValueError("Artist not found")
    tracks = db.artist_library_tracks(mbid, artist["name"])
    more_than_limit = len(tracks)>1000
    tracks = tracks[:1000]
    pages = max(1, (len(tracks)+7)//8)
    page = min(page, pages-1)
    lines = [f"👤 Исполнители · {artist['name']}"]
    if artist["disambiguation"]:
        lines.append(artist["disambiguation"])
    lines.append(f"В медиатеке: {len(tracks)}{'+' if more_than_limit else ''} · {page+1}/{pages}")
    lines.append("Включены подтверждённые записи и совпадения локального имени.")
    rows = [[b("♥ Убрать из избранного" if artist["saved"] else "♡ В избранное",
               f"artistfav:{mbid}:{0 if artist['saved'] else 1}")],
            [b("💿 Альбомы и EP", f"artistdisc:{mbid}:0"), b("📻 Радио", f"artistradio:{mbid}")]]
    if tracks:
        rows.append([b("▶ Слушать сохранённые", f"artistplay:{mbid}")])
    for track in tracks[page*8:page*8+8]:
        lines.append(f"• {track.title[:110]}")
        rows.append([b(f"▶ {track.title[:35]}", f"play:{track.id}"),
                     b("⋯", f"vopen:l:{track.id}")])
    nav = []
    if page:
        nav.append(b("←", f"artistopen:{mbid}:{page-1}"))
    if page+1<pages:
        nav.append(b("→", f"artistopen:{mbid}:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([b("← Исполнители", "artistlist:0")])
    return "\n".join(lines), markup(rows)


def install_artist_handlers(router: Router, db: Library, catalog, send_queue, render_panel):
    async def show(message, mbid, page=0):
        text, controls = artist_page(db, mbid, page)
        await render_panel(message, text, controls, edit=True)

    async def search(message, query):
        if catalog is None:
            await message.answer("Каталог исполнителей отключён.")
            return
        try:
            artists = await search_artists(catalog, query)
        except (CatalogError, ValueError):
            await message.answer("Поиск исполнителя недоступен. Попробуй /artist Имя.")
            return
        db.remember_artists(artists)
        rows = []
        lines = ["👤 Исполнители · Поиск"]
        for artist in artists:
            label = artist.name + (" · " + artist.disambiguation if artist.disambiguation else "")
            rows.append([b(label[:65], f"artistopen:{artist.mbid}:0")])
            lines.append(label[:220])
        if not rows:
            lines.append("Не найдено.")
        rows.append([b("← Исполнители", "artistlist:0")])
        await render_panel(message, "\n".join(lines), markup(rows), edit=True)

    @router.message(Command("artist", "artists"))
    async def artist_command(message: Message):
        query = (message.text or "").partition(" ")[2].strip()
        if query:
            await search(message, query)
        else:
            text, controls = artist_home(db)
            await render_panel(message, text, controls)

    @router.callback_query(F.data == "artistsearch")
    async def artist_search_prompt(callback: CallbackQuery):
        await callback.answer()
        if isinstance(callback.message, Message):
            await callback.message.answer("👤 Поиск исполнителя: ответь названием артиста.",
                reply_markup=ForceReply(input_field_placeholder="Имя исполнителя"))

    @router.message(F.text, F.reply_to_message.text == "👤 Поиск исполнителя: ответь названием артиста.")
    async def artist_search_reply(message: Message, bot: Bot):
        reply = message.reply_to_message
        if reply is not None and reply.from_user is not None and reply.from_user.id == bot.id:
            await search(message, message.text or "")

    @router.callback_query(F.data.startswith("artistlist:"))
    async def list_button(callback: CallbackQuery):
        raw = (callback.data or "").partition(":")[2]
        if not raw.isascii() or not raw.isdigit() or len(raw)>5:
            await callback.answer("Неверная страница", show_alert=True)
            return
        await callback.answer()
        if isinstance(callback.message, Message):
            text, controls = artist_home(db, int(raw))
            await render_panel(callback.message, text, controls, edit=True)

    @router.callback_query(F.data.startswith("artisttrack:"))
    async def track_artist(callback: CallbackQuery):
        raw = (callback.data or "").partition(":")[2]
        if not raw.isascii() or not raw.isdigit() or len(raw)>18:
            await callback.answer("Неверный трек", show_alert=True)
            return
        track = db.get_track(int(raw))
        if track is None:
            await callback.answer("Трек не найден", show_alert=True)
            return
        await callback.answer()
        if not isinstance(callback.message, Message):
            return
        linked = db.recording_for_track(track.id) or {}
        mbid = linked.get("artist_mbid")
        if valid_mbid(mbid):
            if db.get_artist(mbid) is None and catalog is not None:
                try:
                    db.remember_artists([await artist_by_id(catalog, mbid)])
                except CatalogError:
                    pass
            if db.get_artist(mbid) is not None:
                await show(callback.message, mbid)
                return
        await search(callback.message, track.artist)

    @router.callback_query(F.data.startswith("artistopen:") | F.data.startswith("artistfav:"))
    async def open_or_save(callback: CallbackQuery):
        parts = (callback.data or "").split(":")
        if len(parts)!=3 or not valid_mbid(parts[1]) or not parts[2].isascii() or not parts[2].isdigit() or len(parts[2])>5:
            await callback.answer("Неверный исполнитель", show_alert=True)
            return
        action, ident, raw = parts
        if db.get_artist(ident) is None or (action=="artistfav" and raw not in {"0","1"}):
            await callback.answer("Исполнитель не найден", show_alert=True)
            return
        if action=="artistfav":
            db.set_artist_saved(ident, raw=="1")
        await callback.answer()
        if isinstance(callback.message, Message):
            await show(callback.message, ident, int(raw) if action=="artistopen" else 0)

    @router.callback_query(F.data.startswith("artistdisc:"))
    async def discography(callback: CallbackQuery):
        parts = (callback.data or "").split(":")
        if len(parts)!=3 or not valid_mbid(parts[1]) or not parts[2].isascii() or not parts[2].isdigit() or len(parts[2])>3:
            await callback.answer("Неверная страница", show_alert=True)
            return
        artist = db.get_artist(parts[1])
        if artist is None or catalog is None:
            await callback.answer("Каталог недоступен", show_alert=True)
            return
        await callback.answer()
        if not isinstance(callback.message, Message):
            return
        page = int(parts[2])
        try:
            groups, more = await artist_discography(catalog, parts[1], page)
        except CatalogError:
            await callback.message.answer("Дискография временно недоступна.")
            return
        lines = [f"👤 Исполнители · {artist['name']} · Альбомы и EP", f"Страница {page+1}"]
        rows = []
        for group in groups:
            label = f"{group.date[:4] or '—'} · {group.title}"
            lines.append(label[:160])
            rows.append([b(label[:50], f"artistgroup:{group.mbid}")])
        if not groups:
            lines.append("На этой странице нет основных альбомов и EP.")
        nav = []
        if page:
            nav.append(b("←", f"artistdisc:{parts[1]}:{page-1}"))
        if more:
            nav.append(b("→", f"artistdisc:{parts[1]}:{page+1}"))
        if nav:
            rows.append(nav)
        rows.append([b("← Исполнитель", f"artistopen:{parts[1]}:0")])
        await render_panel(callback.message, "\n".join(lines), markup(rows), edit=True)

    @router.callback_query(F.data.startswith("artistgroup:"))
    async def group_button(callback: CallbackQuery):
        ident = (callback.data or "").partition(":")[2]
        if not valid_mbid(ident) or catalog is None:
            await callback.answer("Каталог недоступен", show_alert=True)
            return
        await callback.answer()
        if not isinstance(callback.message, Message):
            return
        from music_bot.albums import album_tracks
        from music_bot.telegram_youtube import album_page
        try:
            release = await group_album(catalog, ident)
            title, artist, tracks = await album_tracks(catalog, release.mbid)
            db.remember_candidates([track for track in tracks if valid_mbid(track.mbid)])
            text, controls = album_page(db, release.mbid, title, artist, tracks, 0)
        except (CatalogError, ValueError):
            await callback.message.answer("Не удалось открыть издание.")
            return
        # Keep the artist discography available behind the new album card.
        await callback.message.answer(text, reply_markup=controls)

    @router.callback_query(F.data.startswith("artistplay:"))
    async def play_artist(callback: CallbackQuery, bot: Bot):
        ident = (callback.data or "").partition(":")[2]
        artist = db.get_artist(ident) if valid_mbid(ident) else None
        tracks = db.artist_library_tracks(ident, artist["name"]) if artist else []
        if not tracks or len(tracks)>1000:
            await callback.answer("Нет аудио или больше 1000 треков", show_alert=True)
            return
        await callback.answer()
        if isinstance(callback.message, Message):
            qid = db.create_queue([t.id for t in tracks], artist["name"], "playlist")
            await send_queue(bot, callback.message.chat.id, qid)

    @router.callback_query(F.data.startswith("artistradio:"))
    async def artist_radio(callback: CallbackQuery):
        ident = (callback.data or "").partition(":")[2]
        artist = db.get_artist(ident) if valid_mbid(ident) else None
        if artist is None or catalog is None:
            await callback.answer("Каталог недоступен", show_alert=True)
            return
        await callback.answer()
        if not isinstance(callback.message, Message):
            return
        try:
            found = await catalog.artist_radio_by_id(ident, difficulty="medium", limit=20)
        except CatalogError:
            await callback.message.answer("Радио временно недоступно.")
            return
        from music_bot.catalog import normalized_identity
        known = {normalized_identity(t.title,t.artist) for t in db.all_tracks(10000)}
        decided = db.catalog_verdicts()
        rows = db.reserve_discovery_suggestions(
            [t for t in found if not t.variant and t.mbid not in decided
             and normalized_identity(t.title,t.artist) not in known], 8)
        db.remember_candidates(rows)
        controls = []
        lines = [f"👤 Исполнители · Радио по {artist['name']}"]
        for track in rows:
            lines.append(f"{track.artist[:65]} — {track.title[:100]}")
            controls.append([b(f"🔎 {track.title[:25]}", f"albtrack:{track.mbid}"),
                             b("🧭 Оценить", f"vopen:c:{track.mbid}")])
            controls.append([InlineKeyboardButton(text="Яндекс ↗", url=yandex_search_url(track.artist, track.title))])
        if not rows:
            lines.append("Новых записей пока нет.")
        controls.append([b("Ещё", f"artistradio:{ident}"), b("← Исполнитель", f"artistopen:{ident}:0")])
        await render_panel(callback.message, "\n".join(lines), markup(controls), edit=True)
