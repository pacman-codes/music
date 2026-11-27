"""Opt-in owner-only /yt and /ytget chat integration.

Never starts a download unless MUSIC_ENABLE_YOUTUBE_DOWNLOAD is enabled.
Only search-produced video IDs, not arbitrary URLs, enter yt-dlp.
"""
from __future__ import annotations

import asyncio
import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery, FSInputFile, InlineKeyboardButton,
    InlineKeyboardMarkup, Message,
)

from music_bot.catalog import CatalogError, MusicCatalog, normalize, valid_mbid
from music_bot.personalized import personalized_discovery
from music_bot.albums import album_query, find_albums, album_tracks, valid_album_id, valid_itunes_id
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


def query_words(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\w+", normalize(value)))


def cached_audio_for_query(db: Library, query: str):
    """Only return an unambiguous exact title + artist match, not a remix guess."""
    wanted = query_words(query)
    if not wanted:
        return None
    matches = []
    for track in db.all_tracks(10000):
        artist = query_words(track.artist)
        title = query_words(track.title)
        if not artist or not title:
            continue
        # Some previously imported YouTube titles already include the artist.
        clean_title = title[len(artist):] if title[:len(artist)] == artist else title
        if not clean_title:
            continue
        if wanted in {artist + clean_title, clean_title + artist}:
            matches.append(track)
    return matches[0] if len(matches) == 1 else None


async def plain_music_search(message: Message, bot: Bot, db: Library,
                             worker: YouTubeSource, *, search_enabled: bool,
                             download_enabled: bool,
                             catalog: MusicCatalog | None = None) -> None:
    query = (message.text or "").strip()
    if query.startswith("/") or not 2 <= len(query) <= 90:
        return
    clean_query, explicit_album = album_query(query)
    albums = []
    if catalog is not None and explicit_album:
        try:
            albums = await find_albums(catalog, clean_query, exact=False)
        except ValueError:
            await message.answer("Укажи название альбома и исполнителя (от 2 до 90 символов).")
            return
        except CatalogError:
            await message.answer("Каталоги альбомов временно недоступны. Попробуй позже или открой сохранённые альбомы: /albums.")
            return
        if not albums:
            await message.answer("Не нашёл альбом. Укажи исполнителя и название.")
            return
        await show_album_results(message, db, catalog, albums)
        return
    track = cached_audio_for_query(db, query)
    if track is not None:
        try:
            await bot.send_audio(message.chat.id, track.file_id,
                                 title=track.title[:64], performer=track.artist[:64],
                                 reply_markup=audio_reaction_keyboard(track.id))
        except TelegramAPIError:
            await message.answer("Telegram не смог отправить сохранённый файл.")
            return
        db.record_single_send(track.id)
        return
    lease = db.acquire_media_import("taste", "plain-music-search", ttl=60)
    if lease is None:
        await message.answer("Уже ищу другую песню.")
        return
    try:
        if catalog is not None:
            try:
                albums = await find_albums(catalog, query, exact=True)
            except (CatalogError, ValueError):
                pass  # An album lookup outage must not break ordinary song search.
            if albums:
                await show_album_results(message, db, catalog, albums,
                                         song_search_hint=search_enabled)
                return
        if not search_enabled:
            await message.answer("Песня не найдена в сохранённых файлах. Внешний поиск отключён.")
            return
        try:
            videos = await worker.search(query, limit=6)
        except (YouTubeError, ValueError):
            videos = []
        if not videos:
            await message.answer("Не нашёл песню. Попробуй указать исполнителя и название.")
            return
        db.remember_youtube_videos(videos)
        # A cached source may now have a playable Telegram file. Reuse it only
        # if the plain text matches its stored artist/title unambiguously.
        track = cached_audio_for_query(db, query)
        if track is not None:
            await bot.send_audio(message.chat.id, track.file_id,
                                 title=track.title[:64], performer=track.artist[:64],
                                 reply_markup=audio_reaction_keyboard(track.id))
            db.record_single_send(track.id)
            return
        # Do not offer album reviews, reactions or production tutorials as audio.
        noise = re.compile(r"\b(?:album review|track review|reaction|tutorial|deconstruction)\b", re.I)
        top = [video for video in videos if not noise.search(video.title)][:3]
        if not top:
            await message.answer("Не нашёл подходящую музыкальную запись.")
            return
        lines = ["🎵 Нашёл:"]
        controls = []
        for i, video in enumerate(top, 1):
            lines.append(f"{i}. {video.title} · {video.channel}"[:240])
            row = [InlineKeyboardButton(text=f"Открыть {i} ↗",
                     url=f"https://www.youtube.com/watch?v={validate_video_id(video.video_id)}")]
            if download_enabled:
                row.insert(0, InlineKeyboardButton(
                    text=f"🎧 Получить {i} (есть разрешение)",
                    callback_data=f"ytget:{video.video_id}"))
            controls.append(row)
        if download_enabled:
            lines.append("Выбери верную версию, если её загрузка разрешена.")
        await message.answer("\n".join(lines),
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=controls))
    finally:
        db.release_media_import("taste", "plain-music-search", lease)


def album_search_keyboard(albums):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"💿 {a.artist[:18]} — {a.title[:30]} ({a.track_count})",
            callback_data=f"album:{a.mbid}:0",
        )] for a in albums
    ])


async def show_album_results(message: Message, db: Library, catalog: MusicCatalog,
                             albums, *, song_search_hint: bool = False):
    """Open an unambiguous release immediately; keep edition choice when needed."""
    if len(albums) == 1:
        album = albums[0]
        try:
            title, artist, tracks = await album_tracks(catalog, album.mbid)
            db.remember_candidates([track for track in tracks if valid_mbid(track.mbid)])
            text, markup = album_page(db, album.mbid, title, artist, tracks, 0)
        except (CatalogError, ValueError):
            # Keep the release button so the user can retry a transient lookup.
            text = "💿 Альбом найден, но трек-лист пока недоступен."
            markup = album_search_keyboard(albums)
        if song_search_hint:
            text += "\n\nОдноимённая песня: /yt " + (message.text or "").strip()
        await message.answer(text, reply_markup=markup)
        return
    text = "💿 Альбомы и EP:"
    if song_search_hint:
        text += "\nОдноимённая песня: /yt " + (message.text or "").strip()
    await message.answer(text, reply_markup=album_search_keyboard(albums))


def album_collection_page(db: Library, page: int = 0):
    size = 10
    if not 0 <= page <= 100000:
        raise ValueError("Invalid album collection page")
    albums = db.saved_albums(offset=page * size, limit=size + 1)
    if not albums and page:
        return album_collection_page(db, 0)
    lines = ["💿 Мои альбомы"]
    rows = []
    for i, album in enumerate(albums[:size], page * size + 1):
        lines.append(f"{i}. {album['artist'][:80]} — {album['title'][:100]} ({album['track_count']})")
        rows.append([InlineKeyboardButton(
            text=f"💿 {album['artist'][:18]} — {album['title'][:30]}",
            callback_data=f"album:{album['mbid']}:0",
        )])
    if not albums:
        lines.append("Пока пусто. Найди альбом и нажми «Добавить альбом».")
    nav = []
    if page:
        nav.append(InlineKeyboardButton(text="←", callback_data=f"albumlist:{page-1}"))
    if len(albums) > size:
        nav.append(InlineKeyboardButton(text="→", callback_data=f"albumlist:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="📂 Плейлисты", callback_data="nav:playlists")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def show_saved_albums(message: Message, db: Library):
    text, markup = album_collection_page(db)
    await message.answer(text, reply_markup=markup)


async def edit_album_message(message: Message, text: str, markup: InlineKeyboardMarkup):
    try:
        await message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            await message.answer(text, reply_markup=markup)


def album_track_ids(db: Library, tracks) -> list[int | None]:
    """One position per album track; never use unconfirmed title matches."""
    resolved = db.resolved_catalog_tracks()
    ids = []
    for track in tracks:
        ident = resolved.get(track.mbid) if valid_mbid(track.mbid) else None
        ids.append(ident if ident is not None and db.get_track(ident) is not None else None)
    return ids


def available_album_ids(db: Library, tracks) -> list[int]:
    return list(dict.fromkeys(ident for ident in album_track_ids(db, tracks) if ident is not None))


def saved_album_ids(db: Library, tracks):
    ids = album_track_ids(db, tracks)
    if any(ident is None for ident in ids) or len(set(ids)) != len(ids):
        return []
    return ids


def album_page(db: Library, mbid: str, title: str, artist: str, tracks, page: int):
    size = 15
    pages = (len(tracks) + size - 1) // size
    if not 0 <= page < pages:
        raise ValueError("Invalid album page")
    lines = [f"💿 {artist} — {title}", f"{len(tracks)} треков" + (f" · {page+1}/{pages}" if pages > 1 else "")]
    if valid_itunes_id(mbid):
        lines.append("Источник: iTunes (резервный каталог)")
    rows = []
    linked = album_track_ids(db, tracks)
    available = sum(ident is not None for ident in linked)
    lines.append(f"Готово к прослушиванию: {available}/{len(tracks)}")
    for i, track in enumerate(tracks[page*size:(page+1)*size], page*size+1):
        local = linked[i-1]
        lines.append(f"{i}. {track.title[:140]}" + (" ✓" if local else ""))
        rows.append([InlineKeyboardButton(
            text=f"{'▶' if local else '🔎'} {i}. {track.title[:35]}",
            callback_data=(f"play:{local}" if local else
                           f"italbtrack:{mbid}:{i-1}" if valid_itunes_id(mbid)
                           else f"albtrack:{track.mbid}"),
        )])
    nav = []
    if page:
        nav.append(InlineKeyboardButton(text="←", callback_data=f"album:{mbid}:{page-1}"))
    if page+1 < pages:
        nav.append(InlineKeyboardButton(text="→", callback_data=f"album:{mbid}:{page+1}"))
    if nav:
        rows.append(nav)
    if saved_album_ids(db, tracks):
        rows.append([InlineKeyboardButton(text="▶ Слушать весь сохранённый альбом",
                                          callback_data=f"albumplay:{mbid}")])
    elif available:
        rows.append([InlineKeyboardButton(text=f"▶ Слушать доступные ({len(set(x for x in linked if x is not None))})",
                                          callback_data=f"albumready:{mbid}")])
    if available < len(tracks):
        first = linked.index(None)
        track = tracks[first]
        rows.append([InlineKeyboardButton(text=f"🔎 Найти следующий: {track.title[:32]}",
                                          callback_data=f"albumnext:{mbid}")])
    rows.append([InlineKeyboardButton(text="↻ Обновить альбом", callback_data=f"album:{mbid}:{page}")])
    saved = db.get_saved_album(mbid) is not None
    rows.append([InlineKeyboardButton(
        text="✓ В коллекции · Удалить" if saved else "➕ Добавить альбом",
        callback_data=f"{'albumdel' if saved else 'albumadd'}:{mbid}:{page}",
    )])
    rows.append([InlineKeyboardButton(text="💿 Мои альбомы", callback_data="albumlist:0")])
    if valid_itunes_id(mbid):
        rows.append([InlineKeyboardButton(text="Альбом в Apple Music ↗",
                                          url="https://music.apple.com/us/album/" + mbid[3:])])
    else:
        rows.append([InlineKeyboardButton(text="Альбом в MusicBrainz ↗",
                                          url="https://musicbrainz.org/release/" + mbid)])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def install_youtube_handlers(router: Router, db: Library, owner_id: int, *,
                             search_enabled: bool, download_enabled: bool,
                             source: YouTubeSource | None = None,
                             catalog: MusicCatalog | None = None,
                             play_album=None) -> None:
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

    async def show_recording_sources(message: Message, mbid: str, *, store_recording=None):
        if not search_enabled:
            await message.answer("YouTube-поиск выключен в .env.")
            return
        recording = store_recording if store_recording is not None else db.get_candidate(mbid.lower())
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
                download_enabled, recording["mbid"] if store_recording is None else None,
            ),
        )

    @router.message(Command("ytfor"))
    async def youtube_for_recording(message: Message):
        parts=(message.text or "").split()
        if len(parts)!=2 or not valid_mbid(parts[1]):
            await message.answer("Пример: /ytfor MBID найденной записи MusicBrainz")
            return
        await show_recording_sources(message, parts[1])

    if catalog is not None:
        @router.message(Command("albums"))
        async def albums_cmd(message: Message):
            await show_saved_albums(message, db)

        @router.callback_query(F.data.startswith("albumlist:"))
        async def album_list(callback: CallbackQuery):
            raw = (callback.data or "").partition(":")[2]
            if not raw.isascii() or not raw.isdigit() or len(raw) > 6:
                await callback.answer("Неверная страница", show_alert=True)
                return
            try:
                text, markup = album_collection_page(db, int(raw))
            except ValueError:
                await callback.answer("Неверная страница", show_alert=True)
                return
            await callback.answer()
            if isinstance(callback.message, Message):
                await edit_album_message(callback.message, text, markup)

        @router.callback_query(F.data.startswith("albumadd:") | F.data.startswith("albumdel:"))
        async def change_album_collection(callback: CallbackQuery):
            parts = (callback.data or "").split(":")
            if (len(parts) != 3 or not valid_album_id(parts[1])
                    or not parts[2].isascii() or not parts[2].isdigit()
                    or len(parts[2]) > 2):
                await callback.answer("Неверный альбом", show_alert=True)
                return
            if not isinstance(callback.message, Message):
                await callback.answer()
                return
            mbid, page = parts[1].lower(), int(parts[2])
            await callback.answer()
            try:
                title, artist, tracks = await album_tracks(catalog, mbid)
                if not 0 <= page < (len(tracks) + 14) // 15:
                    raise ValueError("Invalid album page")
                db.remember_candidates([track for track in tracks if valid_mbid(track.mbid)])
                if parts[0] == "albumadd":
                    db.save_album(mbid, title, artist, len(tracks))
                else:
                    db.remove_saved_album(mbid)
                text, markup = album_page(db, mbid, title, artist, tracks, page)
            except (CatalogError, ValueError):
                await callback.message.answer("Не удалось изменить коллекцию. Попробуй ещё раз.")
                return
            await edit_album_message(callback.message, text, markup)

        @router.callback_query(F.data.startswith("album:"))
        async def open_album(callback: CallbackQuery):
            parts = (callback.data or "").split(":")
            if len(parts) != 3 or not valid_album_id(parts[1]) or not parts[2].isascii() or not parts[2].isdigit() or len(parts[2]) > 2:
                await callback.answer("Неверный альбом", show_alert=True)
                return
            await callback.answer()
            if not isinstance(callback.message, Message):
                return
            try:
                title, artist, tracks = await album_tracks(catalog, parts[1])
                db.remember_candidates([track for track in tracks if valid_mbid(track.mbid)])
                text, markup = album_page(db, parts[1], title, artist, tracks, int(parts[2]))
            except (CatalogError, ValueError):
                await callback.message.answer("Не удалось открыть трек-лист.")
                return
            await edit_album_message(callback.message, text, markup)

        @router.callback_query(F.data.startswith("italbtrack:"))
        async def store_album_track(callback: CallbackQuery):
            parts = (callback.data or "").split(":")
            if (len(parts) != 3 or not valid_itunes_id(parts[1])
                    or not parts[2].isascii() or not parts[2].isdigit()
                    or len(parts[2]) > 2):
                await callback.answer("Неверная запись", show_alert=True)
                return
            await callback.answer()
            if not isinstance(callback.message, Message):
                return
            try:
                _, _, tracks = await album_tracks(catalog, parts[1])
                index = int(parts[2])
                if not 0 <= index < len(tracks):
                    raise ValueError("Invalid track index")
                track = tracks[index]
            except (CatalogError, ValueError):
                await callback.message.answer("Не удалось открыть запись iTunes.")
                return
            await show_recording_sources(callback.message, track.mbid, store_recording={
                "title": track.title, "artist": track.artist, "duration_ms": track.duration_ms,
            })

        @router.callback_query(F.data.startswith("albtrack:"))
        async def album_track(callback: CallbackQuery):
            mbid = (callback.data or "").partition(":")[2]
            if not valid_mbid(mbid):
                await callback.answer("Неверная запись", show_alert=True)
                return
            await callback.answer()
            if isinstance(callback.message, Message):
                await show_recording_sources(callback.message, mbid)

        @router.callback_query(F.data.startswith("albumnext:"))
        async def next_album_track(callback: CallbackQuery):
            mbid = (callback.data or "").partition(":")[2]
            if not valid_album_id(mbid):
                await callback.answer("Неверный альбом", show_alert=True)
                return
            await callback.answer()
            if not isinstance(callback.message, Message):
                return
            try:
                _, _, tracks = await album_tracks(catalog, mbid)
                linked = album_track_ids(db, tracks)
                if None not in linked:
                    await callback.message.answer("Все записи уже доступны.")
                    return
                track = tracks[linked.index(None)]
                if valid_mbid(track.mbid):
                    db.remember_candidates([track])
                    await show_recording_sources(callback.message, track.mbid)
                else:
                    await show_recording_sources(callback.message, track.mbid, store_recording={
                        "title": track.title, "artist": track.artist, "duration_ms": track.duration_ms,
                    })
            except (CatalogError, ValueError):
                await callback.message.answer("Не удалось найти следующую запись.")

        @router.callback_query(F.data.startswith("albumplay:") | F.data.startswith("albumready:"))
        async def play_saved_album(callback: CallbackQuery, bot: Bot):
            mbid = (callback.data or "").partition(":")[2]
            if not valid_album_id(mbid) or play_album is None:
                await callback.answer("Альбом недоступен", show_alert=True)
                return
            await callback.answer()
            if not isinstance(callback.message, Message):
                return
            try:
                title, artist, tracks = await album_tracks(catalog, mbid)
                partial = (callback.data or "").startswith("albumready:")
                ids = available_album_ids(db, tracks) if partial else saved_album_ids(db, tracks)
                if not ids:
                    await callback.message.answer("Нет доступной очереди из подтверждённых записей.")
                    return
                label = f"{artist} — {title}" + (" (доступные)" if partial else "")
                await play_album(bot, callback.message.chat.id, ids, label)
            except (CatalogError, ValueError, TelegramAPIError):
                await callback.message.answer("Не удалось отправить альбом.")

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

    @router.message(F.text, ~F.text.startswith("/"))
    async def plain_search(message: Message, bot: Bot):
        await plain_music_search(message, bot, db, worker,
                                 search_enabled=search_enabled,
                                 download_enabled=download_enabled, catalog=catalog)
