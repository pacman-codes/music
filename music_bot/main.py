"""Owner-only Telegram bot: library, playlists and native audio queues."""
from __future__ import annotations

import asyncio
import logging
import random
import sqlite3

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter, TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile, CallbackQuery, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaAudio, Message, WebAppInfo,
)

from music_bot.anime import AniListCatalog
from music_bot.screen import TmdbCatalog
from music_bot.catalog import MusicCatalog
from music_bot.config import load_settings
from music_bot.fingerprints import AudioRecognition
from music_bot.telegram_anime import install_anime_handlers
from music_bot.telegram_screen import install_screen_handlers
from music_bot.telegram_catalog import install_catalog_handlers
from music_bot.telegram_import import install_import_handlers
from music_bot.telegram_fingerprint import install_fingerprint_handlers
from music_bot.telegram_youtube import install_youtube_handlers, offer_discovery_audio, show_saved_albums
from music_bot.telegram_taste import install_taste_handlers
from music_bot.selection import DYNAMIC_MODES, select_wave_batch
from music_bot.wrapped import wrapped_summary
from music_bot.profile_export import export_profile, profile_json_bytes
from music_bot.github_profile import ProfileSyncError, sync_private_profile
from music_bot.storage import Library, Track
from music_bot.verdicts import LABELS, STATUSES, valid_status, yandex_search_url

logger = logging.getLogger(__name__)

HELP = """🎵 music — личный плеер

Напиши исполнителя и название песни обычным сообщением — поиск без команд.\nОтправь аудио, чтобы добавить трек в библиотеку.
/library — последние треки
/search Текст — поиск в своей библиотеке
/play ID — слушать конкретную песню в Telegram
/find Название — внешний поиск MusicBrainz
/radio Исполнитель — похожая музыка ListenBrainz
/radiotrack ID — радио по конкретной песне
/recommend — персональные открытия по твоим высоким оценкам
/weekly — новые релизы и личные находки недели
/wrapped 30d|365d|all — музыкальная статистика
/tasteexport — приватный JSON всей медиатеки для анализа рекомендаций
/tastesync — обновить музыкальный профиль в приватном GitHub (если настроено)
/ymimport URL — импорт избранного из публичного плейлиста Яндекс Музыки
/ymrefresh — обновить импортированный плейлист Яндекс Музыки
/ytmimport — CSV/TXT из YouTube Music (прикрепи документ с этой подписью)
/sources — импортированные внешние любимые треки и ссылки для прослушивания
/identify ID — подобрать MusicBrainz ID для локального файла
/fingerprint ID — распознать аудио через AcoustID (если включён)
/tag ID — обновить локальные теги после подтверждения MusicBrainz
/unmatch ID — убрать ошибочную ручную привязку
/saved — отложенные находки (пока без аудио)
/decisions — пять плейлистов реакций, с возможностью слушать
/decide ID maybe|add|never|later|interesting — решение по локальному треку
/import MBID URL — разрешённый прямой MP3/M4A → Telegram
/yt Запрос — YouTube-поиск (если включён)
/ytget VIDEO_ID — получить разрешённое аудио в Telegram
/ytfor MBID — подобрать YouTube-версии записи MusicBrainz
/anime Название — поиск аниме AniList
/animelist — список смотрю/просмотрено/в планах
/animestatus ID plan|watch|done|hold|drop
/animeprogress ID Серий — прогресс
/animerate ID 1-10 — оценка аниме
/film Название — TMDB-поиск фильма/сериала
/films — список просмотров
/filmstatus movie|tv ID plan|watch|done|hold|drop
/filmprogress TV_ID Сезон Серия
/filmrate movie|tv ID 1-10
/liked — избранное
/like ID /unlike ID /dislike ID — избранное и исключения
/rate ID 1-10 — оценка трека
/skip ID — вручную отметить пропуск
/finished ID — вручную отметить дослушивание
/playliked — играть понравившиеся
/newplaylist Название — создать подборку
/playlists — список подборок
/playlist ID — треки подборки
/renameplaylist ID Новое название — переименовать
/deleteplaylist ID — удалить (с подтверждением)
/addto ПЛЕЙЛИСТ_ID ТРЕК_ID — добавить
/removefrom ПЛЕЙЛИСТ_ID ТРЕК_ID — убрать
/move ПЛЕЙЛИСТ_ID ТРЕК_ID ПОЗИЦИЯ — переставить
/playplaylist ID — слушать подборку
/wave — Волна 2.0 (знакомое, похожее и новое)
/mix balanced|familiar|explore — настроить Волну
/queue — показать будущие песни
/next ID — поставить песню следующей
/append ID — добавить в конец очереди
/qmove ОТ КУДА — переставить неотправленный трек
/qremove ПОЗИЦИЯ — убрать из очереди
/qfilter rating 7|liked on|today on — фильтры радио
/continue — продолжить последнюю очередь после сбоя кнопок
/discover — новая музыка по вкусу (при включённом YouTube-источнике) или открытия среди сохранённых

Нативный Telegram-плеер играет присланные аудиофайлы.
Бот не видит события реального прослушивания и пропусков.
Кнопки «Пропустил» / «Дослушал» — только ручные отметки."""


class OwnerOnly(BaseMiddleware):
    """Reject foreign accounts and group/channel conversations before dispatch."""

    def __init__(self, owner_id: int):
        self.owner_id = owner_id

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        chat = data.get("event_chat")
        if user is None or user.id != self.owner_id:
            return None
        if chat is None or chat.type != "private":
            return None
        return await handler(event, data)


def arguments(message: Message, number: int):
    parts = (message.text or "").split()
    if len(parts) != number + 1:
        return None
    try:
        ids = tuple(int(v) for v in parts[1:])
    except ValueError:
        return None
    return ids if all(v > 0 for v in ids) else None


def button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def keyboard(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def home_keyboard(webapp_url: str | None = None) -> InlineKeyboardMarkup:
    rows = [
        [button("🌊 Моя волна", "nav:wave"),
         button("🆕 Открытия", "nav:discover")],
        [button("❤️ Понравившиеся", "nav:liked"),
         button("📂 Плейлисты", "nav:playlists")],
        [button("🧭 Пять реакций", "nav:verdicts"),
         button("💿 Мои альбомы", "nav:albums")],
        [button("🎵 Библиотека", "nav:library"),
         button("▶ Продолжить", "nav:continue")],
        [button("⏭ Очередь", "nav:queue"), button("🔎 Поиск", "nav:search")],
    ]
    if webapp_url:
        rows.insert(0, [
            InlineKeyboardButton(
                text="📱 Открыть медиатеку",
                web_app=WebAppInfo(url=webapp_url),
            )
        ])
    return keyboard(rows)


def track_keyboard(track_id: int) -> InlineKeyboardMarkup:
    return keyboard([
        [button("▶ Слушать", f"play:{track_id}")],
        [button("🤔 Может добавлю", f"vset:l:{track_id}:maybe"),
         button("❤️ Добавлю", f"vset:l:{track_id}:add")],
        [button("🚫 Никогда", f"vset:l:{track_id}:never"),
         button("⏳ Не сейчас", f"vset:l:{track_id}:later")],
        [button("🧠 Интересно, но не слушать", f"vset:l:{track_id}:interesting")],
        [button("⭐ Доп. оценка 1–10", f"rateopen:{track_id}"),
         button("➕ В свой плейлист", f"chooseadd:{track_id}")],
        [button("⏭ Следующим", f"enqueue:next:{track_id}"),
         button("＋ В конец очереди", f"enqueue:append:{track_id}")],
    ])


def quick_play_buttons(tracks: list[Track] | tuple[Track, ...],
                       limit: int = 8) -> list[list[InlineKeyboardButton]]:
    """Direct play buttons for search and library lists without many /play IDs."""
    return [
        [button(f"▶ #{t.id} {t.artist[:12]} — {t.title[:24]}", f"play:{t.id}")]
        for t in tracks[:limit]
    ]


def rating_keyboard(track_id: int) -> InlineKeyboardMarkup:
    return keyboard([
        [button(str(n), f"rate:{track_id}:{n}") for n in range(1, 6)],
        [button(str(n), f"rate:{track_id}:{n}") for n in range(6, 11)],
        [button("⏭ Пропустил (вручную)", f"listen:skip:{track_id}")],
        [button("✅ Дослушал (вручную)", f"listen:complete:{track_id}")],
    ])


def format_tracks(tracks: list[Track] | tuple[Track, ...], prefix="") -> str:
    lines = [prefix] if prefix else []
    for t in tracks:
        line = f"#{t.id} · {t.artist} — {t.title}"
        if sum(len(x) + 1 for x in lines) + len(line) > 3500:
            lines.append("…")
            break
        lines.append(line)
    return "\n".join(lines)


def callback_message(callback: CallbackQuery) -> Message | None:
    return callback.message if isinstance(callback.message, Message) else None


def queue_feedback_keyboard(tracks: tuple[Track, ...], queue_id: int,
                            has_more: bool, *, next_cursor: int | None = None
                            ) -> InlineKeyboardMarkup:
    """Compact, native Telegram control surface for this specific audio batch.

    sendMediaGroup cannot attach an inline keyboard to each audio message.
    A single companion card allows selecting any of up to 10 tracks.
    """
    choices = [
        button(f"🧭 {index + 1}. {track.title[:14]}", f"vopen:l:{track.id}")
        for index, track in enumerate(tracks)
    ]
    rows = [choices[offset:offset + 2] for offset in range(0, len(choices), 2)]
    if has_more:
        if next_cursor is None or next_cursor <= 0:
            raise ValueError("Continuation button requires its expected cursor")
        rows.append([button("▶️ Ещё 10", f"more:{queue_id}:{next_cursor}")])
    rows.append([button("🌊 Новая волна", "nav:wave"),
                 button("📂 Плейлисты", "nav:playlists")])
    rows.append([button("⏭ Очередь", "nav:queue")])
    return keyboard(rows)


PLAYLIST_PAGE_SIZE = 8


def playlist_page(db: Library, playlist_id: int, page: int = 0):
    info = db.get_playlist(playlist_id)
    if info is None:
        raise ValueError("Playlist not found")
    total, page, pages, tracks = db.playlist_page(playlist_id, page, PLAYLIST_PAGE_SIZE)
    lines = [f"📂 {info['name']} · {total} треков",
             f"Страница {page+1}/{pages}"]
    rows = []
    if total:
        rows.append([button("▶ Слушать", f"plstart:{playlist_id}:all"),
                     button("🔀 Перемешать", f"plstart:{playlist_id}:shuffle")])
    else:
        lines.append("Пока пусто. Добавь треки из медиатеки.")
    for i, t in enumerate(tracks, page * PLAYLIST_PAGE_SIZE + 1):
        lines.append(f"{i}. {t.artist[:45]} — {t.title[:90]}")
        rows.append([button(f"▶ {i}. {t.title[:36]}", f"plstart:{playlist_id}:{t.id}"),
                     button("⋯", f"pltrack:{playlist_id}:{t.id}:{page}")])
    nav = []
    if page:
        nav.append(button("←", f"plopen:{playlist_id}:{page-1}"))
    if page+1 < pages:
        nav.append(button("→", f"plopen:{playlist_id}:{page+1}"))
    if nav:
        rows.append(nav)
    if info["kind"] == "custom":
        rows.append([button("✏ Переименовать", f"plrename:{playlist_id}"),
                     button("🗑 Удалить", f"pldelete:{playlist_id}")])
    rows.append([button("📂 Все плейлисты", "nav:playlists"),
                 button("🏠 Меню", "nav:home")])
    return "\n".join(lines), keyboard(rows)


def playlist_track_order(db: Library, playlist_id: int, mode: str = "all") -> list[int]:
    tracks = db.playlist_tracks(playlist_id)
    ids = [t.id for t in tracks]
    if mode == "shuffle":
        random.shuffle(ids)
    elif mode != "all":
        if not mode.isascii() or not mode.isdigit() or len(mode) > 18 or int(mode) not in ids:
            raise ValueError("Track not found in playlist")
        ids = ids[ids.index(int(mode)):]
    return ids


def parse_more_callback(data: str) -> tuple[int, int] | None:
    """Bind an old Telegram button to the exact queue position it was issued for."""
    parts = data.split(":")
    if (len(parts) != 3 or parts[0] != "more" or
            not parts[1].isdigit() or not parts[2].isdigit()):
        return None
    queue_id, cursor = int(parts[1]), int(parts[2])
    return (queue_id, cursor) if queue_id > 0 and cursor > 0 else None


async def send_single_track(bot: Bot, db: Library, chat_id: int,
                            track_id: int) -> Track:
    """Send one existing Telegram file_id, without downloading it again.

    Success means Telegram accepted the message, NOT that the user listened.
    """
    track = db.get_track(track_id)
    if track is None:
        raise ValueError("Track not found")
    await bot.send_audio(
        chat_id, audio=track.file_id,
        title=track.title[:64], performer=track.artist[:64],
        caption=f"#{track.id} · {track.artist} — {track.title}"[:950],
        reply_markup=track_keyboard(track.id),
    )
    db.record_single_send(track.id)
    return track


async def deliver_queue(bot: Bot, db: Library, chat_id: int, queue_id: int,
                        send_lock: asyncio.Lock, *,
                        expected_cursor: int | None = None) -> None:
    """Serialize sends and only advance a queue after Telegram accepts its batch."""
    async with send_lock:
        token = db.acquire_media_import("queue", str(queue_id), ttl=180)
        if token is None:
            # Telegram bot and Mini App run in independent processes.
            # Keep a single in-flight delivery per listening queue.
            await bot.send_message(chat_id, "Эта очередь уже отправляется. Подожди ответа.")
            return
        try:
            try:
                # Honor decisions made AFTER a Wave was assembled, while
                # preserving manually selected playlists and review queues.
                db.prune_automatic_pending(queue_id)
                batch = db.peek_queue(queue_id, size=10)
                # A stale/duplicate "Ещё 10" must not advance the radio again.
                # A command starting a *new* queue intentionally has no expected cursor.
                if expected_cursor is not None and batch.cursor != expected_cursor:
                    await bot.send_message(
                        chat_id,
                        "Эта кнопка «Ещё 10» уже использована. Нажми кнопку под "
                        "последней порцией музыки.",
                    )
                    return
                mode = db.queue_kind(queue_id)
                if not batch.tracks and mode in DYNAMIC_MODES:
                    fresh = select_wave_batch(db, mode, queue_id=queue_id)
                    if fresh and db.extend_exhausted_queue(queue_id, [t.id for t in fresh]):
                        batch = db.peek_queue(queue_id, size=10)
                    elif fresh:
                        # Another worker may have just refilled the same queue.
                        batch = db.peek_queue(queue_id, size=10)
            except ValueError:
                await bot.send_message(chat_id, "Очередь не найдена. Создай новую /wave.")
                return
            if not batch.tracks:
                await bot.send_message(chat_id, "Подборка закончилась. 🌊 /wave — новая.")
                return
            try:
                if len(batch.tracks) == 1:
                    t = batch.tracks[0]
                    await bot.send_audio(
                        chat_id, audio=t.file_id,
                        caption=f"#{t.id} · {t.artist} — {t.title}"[:950],
                    )
                else:
                    await bot.send_media_group(
                        chat_id,
                        media=[
                            InputMediaAudio(
                                media=t.file_id,
                                caption=f"#{t.id} · {t.artist} — {t.title}"[:950],
                            )
                            for t in batch.tracks
                        ],
                    )
            except TelegramAPIError:
                # A transport timeout may mean Telegram received a message but
                # did not acknowledge it. Do not advance blindly.
                logger.warning("Telegram media upload/send failed for queue %s", queue_id)
                await bot.send_message(
                    chat_id,
                    "Не получилось отправить аудио. Очередь не продвинута; попробуй ещё раз.",
                )
                return

            if not db.mark_queue_sent(batch):
                logger.warning("Queue %s advanced concurrently; media already sent", queue_id)
                await bot.send_message(chat_id, "Очередь изменилась, создай новую /wave.")
                return

            # Show controls after Telegram accepts the audio batch. This is
            # deliberately a separate message: media groups do not support markup
            # on individual audio messages.
            count = len(batch.tracks)
            dynamic = db.queue_kind(queue_id) in DYNAMIC_MODES
            notice = (
                f"🎧 {batch.label}: отправлено {count}. Следующие треки подберу заново."
                if dynamic else
                f"🎧 {batch.label}: отправлено {count}, осталось {batch.remaining_after}."
                if batch.remaining_after else
                f"✅ {batch.label}: последние {count} треков отправлены."
            )
            notice += (
                "\n🧭 Выбери песню и одну из пяти реакций. "
                "Оценки 1–10 и ручные отметки прослушивания — по желанию."
            )
            try:
                await bot.send_message(
                    chat_id, notice,
                    reply_markup=queue_feedback_keyboard(
                        batch.tracks, queue_id, dynamic or bool(batch.remaining_after),
                        next_cursor=batch.cursor + len(batch.tracks),
                    ),
                )
            except TelegramAPIError:
                # Audio was delivered and cursor was committed already. A missing
                # control card must not be reported as failed music delivery.
                # /continue and the home button recover the listening queue.
                logger.warning("Queue %s sent but controls failed; use /continue", queue_id)

        finally:
            db.release_media_import("queue", str(queue_id), token)

def create_router(db: Library, owner_id: int, webapp_url: str | None = None,
                  catalog: MusicCatalog | None = None,
                  media_hosts: tuple[str, ...] = (),
                  youtube_search_enabled: bool = False,
                  youtube_download_enabled: bool = False,
                  anime_catalog: AniListCatalog | None = None,
                  screen_catalog: TmdbCatalog | None = None,
                  fingerprint_recognizer: AudioRecognition | None = None,
                  profile_github_repo: str | None = None,
                  profile_github_token: str | None = None) -> Router:
    router = Router()
    router.message.middleware(OwnerOnly(owner_id))
    router.callback_query.middleware(OwnerOnly(owner_id))
    send_lock = asyncio.Lock()

    async def start_wave(bot: Bot, chat_id: int, mode: str = "balanced") -> None:
        if (mode == "discover" and catalog is not None
                and youtube_search_enabled and youtube_download_enabled):
            await offer_discovery_audio(bot, db, chat_id, catalog)
            return
        names = {"balanced": "Моя волна", "discover": "Открытия", "favorites": "Любимое"}
        tracks = select_wave_batch(db, mode, count=10)
        if not tracks:
            await bot.send_message(
                chat_id,
                "Для подборки пока нет треков. Пришли аудио или попробуй другой режим.",
            )
            return
        queue_id = db.create_queue([t.id for t in tracks], names[mode], mode)
        await deliver_queue(bot, db, chat_id, queue_id, send_lock)

    async def start_playlist(bot: Bot, chat_id: int, playlist_id: int, mode: str = "all") -> None:
        playlist = db.get_playlist(playlist_id)
        if not playlist:
            await bot.send_message(chat_id, "Плейлист не найден.")
            return
        try:
            ids = playlist_track_order(db, playlist_id, mode)
        except ValueError:
            await bot.send_message(chat_id, "Трек больше не находится в плейлисте.")
            return
        if not ids:
            await bot.send_message(chat_id, "Плейлист пуст.")
            return
        label = playlist["name"] + (" · случайный порядок" if mode == "shuffle" else "")
        queue_id = db.create_queue(ids, label, "playlist")
        await deliver_queue(bot, db, chat_id, queue_id, send_lock)

    async def show_playlist_page(message: Message, playlist_id: int, page: int = 0, *, edit=False):
        try:
            text, markup = playlist_page(db, playlist_id, page)
        except ValueError:
            await message.answer("Плейлист не найден.")
            return
        if edit:
            try:
                await message.edit_text(text, reply_markup=markup)
                return
            except TelegramBadRequest as exc:
                if "message is not modified" in str(exc).lower():
                    return
        await message.answer(text, reply_markup=markup)

    async def show_playlists(message: Message) -> None:
        playlists = db.playlists()
        rows = [[button(f"{'❤️' if p['kind']=='liked' else '📂'} {p['name'][:38]} · {p['count']}",
                        f"plopen:{p['id']}:0"),
                 button("▶", f"playlist:{p['id']}")] for p in playlists[:30]]
        rows.append([button("＋ Создать плейлист", "plnew")])
        rows.append([button("💿 Альбомы", "nav:albums"), button("🧭 Реакции", "nav:verdicts")])
        rows.append([button("🏠 Меню", "nav:home")])
        await message.answer("📂 Мои плейлисты\nНажми название, чтобы открыть треки, или ▶ для запуска.",
                             reply_markup=keyboard(rows))

    async def show_library(message: Message) -> None:
        tracks = db.all_tracks(20)
        await message.answer(
            format_tracks(tracks, "🎵 Библиотека:") if tracks
            else "Библиотека пуста. Пришли аудио.",
            reply_markup=keyboard(quick_play_buttons(tracks)) if tracks else None,
        )

    async def show_liked(message: Message) -> None:
        await show_playlist_page(message, db.liked_id())

    async def show_decisions(message: Message) -> None:
        counts = db.verdict_counts()
        rows = [
            [button(f"{LABELS[status]} · {counts[status]}", f"vlist:{status}:0")]
            for status in STATUSES
        ]
        await message.answer(
            "🧭 Пять реакций. Каждая песня только в одной категории.\n"
            "▶ Слушать в Telegram — если файл уже есть; "
            "🎧 Яндекс — внешний поиск и воспроизведение в сервисе.",
            reply_markup=keyboard(rows),
        )

    async def show_decision_list(message: Message, status: str, page: int = 0) -> None:
        if not valid_status(status) or not 0 <= page <= 1000:
            await message.answer("Некорректный список.")
            return
        total = db.verdict_counts()[status]
        page_size = 8
        page_count = max(1, (total + page_size - 1) // page_size)
        page = min(page, page_count - 1)
        shown = db.verdict_items(status, limit=page_size, offset=page*page_size)
        lines = [f"{LABELS[status]} — {total} треков · {page+1}/{page_count}"]
        rows: list[list[InlineKeyboardButton]] = []
        for i, entry in enumerate(shown, start=page * page_size + 1):
            artist = str(entry["artist"]).replace("\n", " ").replace("\r", " ")
            title = str(entry["title"]).replace("\n", " ").replace("\r", " ")
            lines.append(
                f"{i}. {artist[:90]} — {title[:110]} "
                + ("▶ Telegram" if entry["track_id"] is not None else "🎧 Яндекс")
            )
            kind = "l" if entry["source"] == "local" else "c"
            ident = str(entry["track_id"] if kind == "l" else entry["mbid"])
            row = [
                button("🧭 Изменить", f"vopen:{kind}:{ident}"),
            ]
            if entry["track_id"] is not None:
                row.insert(0, button("▶ Слушать", f"play:{entry['track_id']}"))
            else:
                row.insert(0, InlineKeyboardButton(
                    text="🎧 Найти в Яндексе",
                    url=yandex_search_url(artist, title),
                ))
            rows.append(row)
        nav = []
        if page > 0:
            nav.append(button("◀ Назад", f"vlist:{status}:{page-1}"))
        if page + 1 < page_count:
            nav.append(button("Дальше ▶", f"vlist:{status}:{page+1}"))
        if nav:
            rows.append(nav)
        rows.append([button("🧭 Все пять списков", "vhome")])
        if db.verdict_playable_ids(status, limit=1):
            rows.append([button("▶ Слушать доступные в Telegram", f"vplay:{status}")])
        await message.answer(
            "\n".join(lines)[:3900], reply_markup=keyboard(rows),
        )

    @router.message(Command("decisions", "verdicts"))
    async def decision_home_cmd(message: Message):
        await show_decisions(message)

    @router.message(Command("decide"))
    async def decision_cmd(message: Message):
        raw = (message.text or "").split()
        if len(raw) != 3 or not raw[1].isdecimal() or not valid_status(raw[2]):
            await message.answer("Пример: /decide 12 maybe|add|never|later|interesting")
            return
        try:
            db.set_track_verdict(int(raw[1]), raw[2])
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await message.answer(f"🧭 #{raw[1]} → {LABELS[raw[2]]}")

    @router.callback_query(F.data == "vhome")
    async def decision_home_button(callback: CallbackQuery):
        await callback.answer()
        message = callback_message(callback)
        if message is not None:
            await show_decisions(message)

    @router.callback_query(F.data.startswith("vlist:"))
    async def decision_list_button(callback: CallbackQuery):
        raw = (callback.data or "").split(":")
        if (len(raw) != 3 or not valid_status(raw[1]) or
                not raw[2].isdecimal() or int(raw[2]) > 1000):
            await callback.answer("Неверный список", show_alert=True)
            return
        await callback.answer()
        message = callback_message(callback)
        if message is not None:
            await show_decision_list(message, raw[1], int(raw[2]))

    @router.callback_query(F.data.startswith("vplay:"))
    async def decision_play_button(callback: CallbackQuery, bot: Bot):
        status = (callback.data or "").partition(":")[2]
        if not valid_status(status):
            await callback.answer("Неверный плейлист", show_alert=True)
            return
        track_ids = db.verdict_playable_ids(status)
        if not track_ids:
            await callback.answer("Пока нет загруженных аудиофайлов", show_alert=True)
            return
        await callback.answer()
        message = callback_message(callback)
        if message is None:
            return
        queue_id = db.create_queue(track_ids, LABELS[status], "decision")
        await deliver_queue(bot, db, message.chat.id, queue_id, send_lock)

    @router.callback_query(F.data.startswith("vopen:"))
    async def decision_picker(callback: CallbackQuery):
        raw = (callback.data or "").split(":")
        if len(raw) != 3 or raw[1] not in ("l", "c"):
            await callback.answer("Неверная запись", show_alert=True)
            return
        kind, ident = raw[1], raw[2]
        if kind == "l":
            if not ident.isdecimal() or int(ident) <= 0:
                await callback.answer("Неверный трек", show_alert=True)
                return
            track = db.get_track(int(ident))
            item = {"artist": track.artist, "title": track.title} if track else None
        else:
            item = db.get_candidate(ident) if len(ident) == 36 else None
        if item is None:
            await callback.answer("Трек не найден", show_alert=True)
            return
        await callback.answer()
        message = callback_message(callback)
        if message is not None:
            rows = [
                [button(LABELS[status], f"vset:{kind}:{ident}:{status}")]
                for status in STATUSES
            ]
            await message.answer(
                f"🧭 {item['artist']} — {item['title']}\nКак поступить?",
                reply_markup=keyboard(rows),
            )

    @router.callback_query(F.data.startswith("vset:"))
    async def decision_set_button(callback: CallbackQuery):
        raw = (callback.data or "").split(":")
        if (len(raw) != 4 or raw[1] not in ("l", "c")
                or not valid_status(raw[3])):
            await callback.answer("Неверная реакция", show_alert=True)
            return
        kind, ident, status = raw[1], raw[2], raw[3]
        try:
            if kind == "l":
                if not ident.isdecimal() or int(ident) <= 0:
                    raise ValueError("Неверный трек")
                db.set_track_verdict(int(ident), status)
            else:
                if len(ident) != 36:
                    raise ValueError("Неверная запись каталога")
                db.set_catalog_verdict(ident, status)
        except ValueError as exc:
            await callback.answer(str(exc)[:160], show_alert=True)
            return
        await callback.answer(f"Сохранено: {LABELS[status]}")

    @router.message(Command("start", "help"))
    async def start(message: Message):
        await message.answer(HELP, reply_markup=home_keyboard(webapp_url))

    @router.message(F.audio)
    async def audio(message: Message):
        a = message.audio
        track = db.add_track(
            a.file_id, a.file_unique_id,
            a.title or a.file_name or "Без названия",
            a.performer or "Неизвестный исполнитель",
        )
        await message.answer(
            f"Сохранено #{track.id}: {track.artist} — {track.title}"[:3500],
            reply_markup=track_keyboard(track.id),
        )

    @router.message(Command("play"))
    async def play_command(message: Message, bot: Bot):
        parsed = arguments(message, 1)
        if parsed is None:
            await message.answer("Пример: /play 12")
            return
        try:
            await send_single_track(bot, db, message.chat.id, parsed[0])
        except ValueError:
            await message.answer("Трек не найден. Открой /library.")
        except TelegramAPIError:
            await message.answer("Telegram не смог отправить песню. Попробуй позже.")

    @router.callback_query(F.data.startswith("play:"))
    async def play_button(callback: CallbackQuery, bot: Bot):
        raw = (callback.data or "").partition(":")[2]
        message = callback_message(callback)
        if not raw.isdigit() or int(raw) <= 0 or message is None:
            await callback.answer("Неверный трек", show_alert=True)
            return
        await callback.answer()
        try:
            await send_single_track(bot, db, message.chat.id, int(raw))
        except ValueError:
            await message.answer("Трек не найден.")
        except TelegramAPIError:
            await message.answer("Telegram не смог отправить песню.")

    @router.callback_query(F.data.startswith("rateopen:"))
    async def rating_picker(callback: CallbackQuery):
        try:
            track_id = int((callback.data or "").split(":")[1])
        except (ValueError, IndexError):
            await callback.answer("Некорректный трек", show_alert=True)
            return
        message = callback_message(callback)
        if message is None:
            await callback.answer("Не удалось открыть оценку", show_alert=True)
            return
        track = db.get_track(track_id)
        if track is None:
            await callback.answer("Трек не найден", show_alert=True)
            return
        current = db.ratings().get(track_id)
        await callback.answer()
        await message.answer(
            f"⭐ {track.artist} — {track.title} (#{track_id}). "
            f"Оценка: {str(current)+'/10' if current else 'нет'}.\n"
            "Пропуск и дослушивание здесь отмечаются вручную; "
            "Telegram не сообщает о них автоматически.",
            reply_markup=rating_keyboard(track_id),
        )

    @router.callback_query(F.data.startswith("rate:"))
    async def rate_button(callback: CallbackQuery):
        try:
            _, raw_track, raw_score = (callback.data or "").split(":")
            score = int(raw_score)
            db.set_rating(int(raw_track), score)
        except (ValueError, IndexError):
            await callback.answer("Оценка должна быть от 1 до 10", show_alert=True)
            return
        await callback.answer(f"Оценено на {score}/10 ⭐")

    @router.callback_query(F.data.startswith("listen:"))
    async def listen_button(callback: CallbackQuery):
        try:
            _, event, raw_track = (callback.data or "").split(":")
            changed = db.record_playback_event(
                int(raw_track), event, source="manual",
                dedupe_key=f"tg:callback:{callback.id}",
            )
        except (ValueError, IndexError):
            await callback.answer("Не удалось записать отметку", show_alert=True)
            return
        await callback.answer(
            ("⏭ Пропуск учтён" if event == "skip" else "✅ Дослушивание учтено")
            if changed else "Эта отметка уже учтена",
        )

    @router.callback_query(F.data.startswith("like:"))
    async def like_button(callback: CallbackQuery):
        try:
            track_id = int(callback.data.split(":", 1)[1])
            db.like(track_id)
        except ValueError:
            await callback.answer("Трек не найден", show_alert=True)
            return
        await callback.answer("Добавлено в понравившиеся ❤️")

    @router.callback_query(F.data.startswith("dislike:"))
    async def dislike_button(callback: CallbackQuery):
        try:
            db.dislike(int(callback.data.split(":", 1)[1]))
        except ValueError:
            await callback.answer("Трек не найден", show_alert=True)
            return
        await callback.answer("Больше не буду предлагать 👎")

    @router.callback_query(F.data.startswith("chooseadd:"))
    async def choose_add(callback: CallbackQuery):
        try:
            track_id = int(callback.data.split(":", 1)[1])
        except ValueError:
            await callback.answer("Неверная кнопка", show_alert=True)
            return
        custom = [p for p in db.playlists() if p["kind"] == "custom"][:20]
        message = callback_message(callback)
        await callback.answer()
        if message is None:
            return
        rows = [
            [button(f"➕ {p['name'][:40]}", f"doadd:{p['id']}:{track_id}")]
            for p in custom
        ]
        rows.append([button("＋ Новый плейлист", f"plnew:{track_id}")])
        await message.answer("В какой плейлист добавить?", reply_markup=keyboard(rows))

    @router.callback_query(F.data.startswith("doadd:"))
    async def do_add(callback: CallbackQuery):
        try:
            _, playlist_id, track_id = callback.data.split(":")
            added = db.add_to(int(playlist_id), int(track_id))
        except (ValueError, TypeError):
            await callback.answer("Плейлист или трек не найден", show_alert=True)
            return
        await callback.answer("Добавлено ✅" if added else "Уже в плейлисте")

    @router.callback_query(F.data.startswith("more:"))
    async def more(callback: CallbackQuery, bot: Bot):
        message = callback_message(callback)
        await callback.answer()
        if message is None:
            return
        parsed = parse_more_callback(callback.data or "")
        if parsed is None:
            await message.answer(
                "Кнопка устарела. Запусти /wave или выбери последнюю порцию."
            )
            return
        queue_id, expected_cursor = parsed
        await deliver_queue(
            bot, db, message.chat.id, queue_id, send_lock,
            expected_cursor=expected_cursor,
        )

    @router.callback_query(F.data.startswith("playlist:"))
    async def play_playlist_button(callback: CallbackQuery, bot: Bot):
        message = callback_message(callback)
        await callback.answer()
        if message:
            try:
                playlist_id = int(callback.data.split(":", 1)[1])
            except ValueError:
                await message.answer("Неверный плейлист")
                return
            await start_playlist(bot, message.chat.id, playlist_id)

    @router.callback_query(F.data.startswith("nav:"))
    async def nav(callback: CallbackQuery, bot: Bot):
        message = callback_message(callback)
        await callback.answer()
        if message is None:
            return
        action = callback.data.split(":", 1)[1]
        if action == "home":
            await message.answer("🎵 LAIN · Твоя медиатека", reply_markup=home_keyboard(webapp_url))
        elif action == "search":
            await message.answer("Напиши исполнителя и название песни обычным сообщением.")
        elif action == "queue":
            await queue_cmd(message)
        elif action == "wave":
            await start_wave(bot, message.chat.id)
        elif action == "discover":
            await start_wave(bot, message.chat.id, "discover")
        elif action == "liked":
            await show_liked(message)
        elif action == "playlists":
            await show_playlists(message)
        elif action == "albums":
            if catalog is None:
                await message.answer("Каталог альбомов отключён.")
            else:
                await show_saved_albums(message, db)
        elif action == "verdicts":
            await show_decisions(message)
        elif action == "library":
            await show_library(message)
        elif action == "continue":
            last = db.latest_queue_id()
            if last is None:
                await message.answer("Очередей пока нет. Запусти /wave.")
            else:
                await deliver_queue(bot, db, message.chat.id, last, send_lock)

    @router.message(Command("continue"))
    async def continue_cmd(message: Message, bot: Bot):
        last = db.latest_queue_id()
        if last is None:
            await message.answer("Очередей пока нет. Запусти /wave.")
            return
        await deliver_queue(bot, db, message.chat.id, last, send_lock)

    @router.message(Command("library"))
    async def library_cmd(message: Message):
        await show_library(message)

    @router.message(Command("search"))
    async def search_cmd(message: Message):
        query = (message.text or "").partition(" ")[2].strip()
        if not query:
            await message.answer("Пример: /search Linkin Park")
            return
        tracks = db.search_tracks(query)
        await message.answer(
            format_tracks(tracks, "🔎 Результаты:") if tracks else "Ничего не найдено.",
            reply_markup=keyboard(quick_play_buttons(tracks)) if tracks else None,
        )

    @router.message(Command("liked"))
    async def liked_cmd(message: Message):
        await show_liked(message)

    @router.message(Command("like", "unlike", "dislike"))
    async def feedback_cmd(message: Message):
        ids = arguments(message, 1)
        if not ids:
            await message.answer("Нужен номер трека, например /like 12")
            return
        action = message.text.split()[0].split("@")[0][1:]
        try:
            if action == "like":
                changed = db.like(ids[0])
            elif action == "unlike":
                changed = db.unlike(ids[0])
            else:
                db.dislike(ids[0])
                changed = True
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await message.answer("Готово ✅" if changed else "Уже было установлено.")

    @router.message(Command("rate"))
    async def rate_command(message: Message):
        ids = arguments(message, 2)
        if not ids or not 1 <= ids[1] <= 10:
            await message.answer("Пример: /rate 12 8 — оценка от 1 до 10")
            return
        try:
            db.set_rating(ids[0], ids[1])
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await message.answer(
            f"⭐ Трек #{ids[0]}: {ids[1]}/10. Оценка не меняет избранное."
        )

    @router.message(Command("skip", "finished"))
    async def report_command(message: Message):
        ids = arguments(message, 1)
        if not ids:
            await message.answer("Пример: /skip 12 или /finished 12")
            return
        action = message.text.split()[0].split("@")[0][1:]
        event = "skip" if action == "skip" else "complete"
        try:
            db.record_playback_event(
                ids[0], event, source="manual",
                dedupe_key=f"tg:message:{message.chat.id}:{message.message_id}",
            )
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await message.answer(
            "⏭ Отметка пропуска сохранена." if event == "skip"
            else "✅ Отметка дослушивания сохранена."
        )

    @router.message(Command("playliked"))
    async def playliked(message: Message, bot: Bot):
        await start_playlist(bot, message.chat.id, db.liked_id())

    @router.message(Command("newplaylist"))
    async def new_playlist(message: Message):
        name = (message.text or "").partition(" ")[2].strip()
        try:
            playlist_id = db.create_playlist(name)
        except (ValueError, sqlite3.IntegrityError):
            await message.answer("Название пустое, длиннее 80 символов или занято.")
            return
        await message.answer(f"📂 Создан плейлист #{playlist_id}: {name}")

    @router.message(Command("playlists"))
    async def playlists_cmd(message: Message):
        await show_playlists(message)

    @router.message(Command("playlist"))
    async def playlist_cmd(message: Message):
        ids = arguments(message, 1)
        if not ids:
            await message.answer("Пример: /playlist 2")
            return
        await show_playlist_page(message, ids[0])

    @router.message(Command("renameplaylist"))
    async def renameplaylist(message: Message):
        rest = (message.text or "").partition(" ")[2].strip()
        playlist_id, separator, name = rest.partition(" ")
        if not separator or not playlist_id.isdecimal():
            await message.answer("Пример: /renameplaylist 2 Новый плейлист")
            return
        try:
            db.rename_playlist(int(playlist_id), name)
        except (ValueError, sqlite3.IntegrityError):
            await message.answer("Не удалось переименовать. Проверь ID и название.")
            return
        await message.answer("Плейлист переименован ✅")

    @router.message(Command("deleteplaylist"))
    async def deleteplaylist(message: Message):
        ids = arguments(message, 1)
        if not ids:
            await message.answer("Пример: /deleteplaylist 2")
            return
        info = db.get_playlist(ids[0])
        if not info or info["kind"] == "liked":
            await message.answer("Удалять можно только свои плейлисты.")
            return
        await message.answer(
            f"Точно удалить «{info['name']}»? Треки останутся в библиотеке.",
            reply_markup=keyboard([[
                button("🗑 Да, удалить", f"confirmdelete:{ids[0]}"),
                button("Отмена", "canceldelete"),
            ]]),
        )

    @router.callback_query(F.data.startswith("confirmdelete:"))
    async def confirmdelete(callback: CallbackQuery):
        try:
            db.delete_playlist(int(callback.data.split(":", 1)[1]))
        except ValueError:
            await callback.answer("Плейлист уже удалён или недоступен", show_alert=True)
            return
        await callback.answer("Плейлист удалён")
        message = callback_message(callback)
        if message:
            await message.edit_text("🗑 Плейлист удалён, треки сохранены.")

    @router.callback_query(F.data == "canceldelete")
    async def canceldelete(callback: CallbackQuery):
        await callback.answer("Отменено")
        message = callback_message(callback)
        if message:
            await message.edit_text("Удаление отменено.")

    @router.message(Command("addto", "removefrom"))
    async def playlist_membership(message: Message):
        ids = arguments(message, 2)
        if not ids:
            await message.answer("Пример: /addto 2 12 или /removefrom 2 12")
            return
        try:
            action = message.text.split()[0].split("@")[0][1:]
            changed = (
                db.add_to(*ids) if action == "addto" else db.remove_from(*ids)
            )
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await message.answer("Готово ✅" if changed else "Изменений нет.")

    @router.message(Command("move"))
    async def move_cmd(message: Message):
        ids = arguments(message, 3)
        if not ids:
            await message.answer("Пример: /move 2 12 1")
            return
        try:
            db.move_track(*ids)
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await message.answer("Порядок обновлён ✅")

    @router.message(Command("playplaylist"))
    async def playplaylist_cmd(message: Message, bot: Bot):
        ids = arguments(message, 1)
        if not ids:
            await message.answer("Пример: /playplaylist 2")
            return
        await start_playlist(bot, message.chat.id, ids[0])

    @router.message(Command("queue"))
    async def queue_cmd(message: Message):
        qid = db.latest_queue_id()
        if qid is None:
            await message.answer("Очереди нет. Запусти /wave или плейлист.")
            return
        state = db.queue_snapshot(qid, limit=15)
        text = (
            f"⏭ Очередь #{qid} · {state['label']}\n"
            f"Отправлено: {state['cursor']}; ожидает: {state['pending_total']}.\n"
        )
        text += "\n".join(
            f"{item['position']+1}. {item['artist']} — {item['title']}"[:180]
            for item in state["upcoming"]
        )
        if state["is_dynamic"]:
            filters = db.get_queue_filters(qid)
            text += (
                f"\nФильтры: оценка от {filters['min_rating']}, "
                f"избранное={filters['liked_only']}, "
                f"не отправлялось сегодня={filters['no_repeat_today']}."
            )
        text += "\n/next ID · /append ID · /qmove 11 13 · /qremove 13 · /continue"
        rows = []
        if state["pending_total"] or state["is_dynamic"]:
            rows.append([button("▶ Следующая порция", f"qsend:{qid}:{state['cursor']}")])
        rows.append([button("↻ Обновить", "nav:queue"), button("📂 Плейлисты", "nav:playlists")])
        await message.answer(text[:3900], reply_markup=keyboard(rows))

    @router.message(Command("next", "append"))
    async def queue_add_cmd(message: Message):
        args = arguments(message, 1)
        if args is None:
            await message.answer("Пример: /next 12 или /append 12")
            return
        qid = db.latest_queue_id()
        if qid is None:
            await message.answer("Сначала запусти Волну или плейлист.")
            return
        kind = (message.text or "").split()[0].split("@")[0][1:]
        try:
            result = db.edit_queue(qid, kind, track_id=args[0])
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await message.answer(
            f"⏭ Добавлено в очередь #{qid}. Ожидает {result['pending_total']}. "
            "Продолжение: /continue"
        )

    @router.message(Command("qmove"))
    async def queue_move_cmd(message: Message):
        args = arguments(message, 2)
        if args is None or min(args) < 1:
            await message.answer("Пример: /qmove 11 13 (номера из /queue)")
            return
        qid = db.latest_queue_id()
        if qid is None:
            await message.answer("Очереди нет.")
            return
        try:
            db.edit_queue(qid, "move", position=args[0]-1, to_position=args[1]-1)
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await message.answer("✅ Порядок сохранён. /queue")

    @router.message(Command("qremove"))
    async def queue_remove_cmd(message: Message):
        args = arguments(message, 1)
        if args is None or args[0] < 1:
            await message.answer("Пример: /qremove 11")
            return
        qid = db.latest_queue_id()
        if qid is None:
            await message.answer("Очереди нет.")
            return
        try:
            db.edit_queue(qid, "remove", position=args[0]-1)
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await message.answer("✅ Песня убрана из будущей очереди. /queue")

    @router.message(Command("qfilter"))
    async def queue_filter_cmd(message: Message):
        qid = db.latest_queue_id()
        if qid is None:
            await message.answer("Сначала запусти /wave.")
            return
        words = (message.text or "").split()
        if len(words) != 3:
            await message.answer(
                "/qfilter rating 7 · /qfilter liked on|off · "
                "/qfilter today on|off"
            )
            return
        try:
            active = db.get_queue_filters(qid)
            field, value = words[1].lower(), words[2].lower()
            if field == "rating":
                active["min_rating"] = int(value)
            elif field in {"liked", "today"} and value in {"on","off"}:
                active["liked_only" if field == "liked" else "no_repeat_today"] = value == "on"
            else:
                raise ValueError("Unknown filter")
            db.set_queue_filters(qid, **active)
        except (ValueError, TypeError) as exc:
            await message.answer(
                "Неверный фильтр. /qfilter rating 7 · "
                "/qfilter liked on · /qfilter today on"
            )
            return
        await message.answer(
            "Фильтры применятся к следующей новой порции радио. /queue"
        )

    @router.message(Command("tag"))
    async def tag_cmd(message: Message):
        ids=arguments(message,1)
        if ids is None:
            await message.answer("Пример: /tag 12. Сначала подтверди MBID.")
            return
        try:
            track=db.apply_confirmed_recording_tags(ids[0])
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await message.answer(
            f"🏷 Локальные теги обновлены: {track.artist} — {track.title}. "
            "Сам файл Telegram не изменялся."
        )

    @router.message(Command("tastesync"))
    async def taste_sync_cmd(message: Message):
        if len((message.text or "").split()) != 1:
            await message.answer("Для обновления приватного GitHub: /tastesync")
            return
        if not profile_github_repo or not profile_github_token:
            await message.answer(
                "Приватный GitHub ещё не настроен. "
                "Используй /tasteexport и передай JSON вручную."
            )
            return
        try:
            result = await sync_private_profile(
                export_profile(db),
                repo=profile_github_repo,
                token=profile_github_token,
            )
        except ProfileSyncError as exc:
            await message.answer(str(exc))
            return
        await message.answer(
            "🔒 Музыкальный профиль обновлён в проверенном приватном GitHub: "
            f"{result['repo']}/{result['path']}. "
            "Теперь можно попросить меня сравнить изменения и улучшить рекомендации."
        )

    @router.message(Command("tasteexport"))
    async def taste_export_cmd(message: Message):
        """Owner-only document, never published to GitHub."""
        if len((message.text or "").split()) != 1:
            await message.answer("Для экспорта всей медиатеки: /tasteexport")
            return
        profile = export_profile(db)
        content = profile_json_bytes(profile)
        if len(content) > 20 * 1024 * 1024:
            await message.answer(
                "Профиль слишком большой для Telegram. "
                "Выгрузи его через python -m music_bot.profile_export."
            )
            return
        label = profile["generated_at_utc"][:10].replace("-", "")
        await message.answer_document(
            BufferedInputFile(content, filename=f"music-profile-{label}.json"),
            caption=(
                f"🎵 Личный профиль: {profile['total_tracks']} треков. "
                "Без файлов Telegram и токенов. Передай мне файл для анализа "
                "или сохрани в приватном репозитории. Не публикуй открыто."
            ),
        )

    @router.message(Command("wrapped"))
    async def wrapped_cmd(message: Message):
        raw=(message.text or "").split()
        period=raw[1] if len(raw)==2 else "30d"
        try:
            info=wrapped_summary(db,period)
        except ValueError:
            await message.answer("Пример: /wrapped 7d, /wrapped 30d, /wrapped 365d")
            return
        text=(
            f"📊 Твой Music Wrapped ({period})\n"
            f"В библиотеке: {info['library_tracks']} песен\n"
            f"В избранном: {info['favorites_count']}\n"
            f"Отправлено в Telegram: {info['deliveries']} сообщений с аудио, "
            f"уникальных треков: {info['distinct_sent_tracks']}\n"
            f"Твои оценки: {info['rated_count']}, средняя: "
            f"{info['average_rating'] or '—'}/10\n"
            f"Ручных пропусков: {info['manual_skips']}, "
            f"ручных дослушиваний: {info['manual_completions']}\n"
        )
        if info["top_artists"]:
            text+="\nЧаще всего отправлял:\n"
            text+="\n".join(
                f"• {x['artist']} — {x['deliveries']} отправок"
                for x in info["top_artists"][:5]
            )
        if info["favorite_artists"]:
            text+="\nЛюбимые артисты:\n"+", ".join(
                x["artist"] for x in info["favorite_artists"][:4]
            )
        text+="\nℹ️ Telegram не передаёт боту реальные прослушивания."
        await message.answer(text[:3900])

    @router.message(Command("mix"))
    async def mix_cmd(message: Message):
        raw = (message.text or "").split()
        if len(raw) == 1:
            await message.answer(
                "Текущий режим Волны: " + db.get_wave_preset()
                + ". Варианты: /mix balanced, /mix familiar, /mix explore."
            )
            return
        try:
            if len(raw) != 2:
                raise ValueError("Invalid preset")
            db.set_wave_preset(raw[1].lower())
        except ValueError:
            await message.answer("Варианты: /mix balanced|familiar|explore")
            return
        await message.answer("🌊 Режим Волны сохранён: " + db.get_wave_preset())

    @router.message(Command("wave", "discover"))
    async def wave_cmd(message: Message, bot: Bot):
        action = message.text.split()[0].split("@")[0][1:]
        await start_wave(
            bot, message.chat.id,
            "discover" if action == "discover" else "balanced",
        )

    if catalog is not None:
        install_catalog_handlers(router, db, catalog)
    install_import_handlers(router, db, owner_id, media_hosts)
    install_fingerprint_handlers(router, db, fingerprint_recognizer)
    install_taste_handlers(router, db)
    if anime_catalog is not None:
        install_anime_handlers(router, db, anime_catalog)
    if screen_catalog is not None:
        install_screen_handlers(router, db, screen_catalog)
    async def play_album(bot: Bot, chat_id: int, ids: list[int], label: str):
        queue_id = db.create_queue(ids, label, "playlist")
        await deliver_queue(bot, db, chat_id, queue_id, send_lock)

    # Plain text music search is a fallback after all explicit command handlers.
    install_youtube_handlers(
        router, db, owner_id, search_enabled=youtube_search_enabled,
        download_enabled=youtube_download_enabled, catalog=catalog,
        play_album=play_album,
    )
    return router



async def await_telegram_ready(bot: Bot) -> None:
    """Respect Bot API flood control before entering aiogram's polling loop.

    Dispatcher.start_polling() calls bot.me() during startup. If getMe is
    rate-limited, an uncaught TelegramRetryAfter otherwise exits the process;
    Docker immediately restarts it and can prolong the flood window.
    Calling bot.me() here also caches the result for start_polling().
    """
    while True:
        try:
            await bot.me()
            return
        except TelegramRetryAfter as exc:
            # Give the server the requested cooldown plus a small margin,
            # without ever turning a transient 429 into a hot restart loop.
            delay = max(1, int(exc.retry_after) + 1)
            logger.warning(
                "Telegram getMe rate-limited; retrying after %s seconds", delay
            )
            await asyncio.sleep(delay)


async def main():
    logging.basicConfig(level=logging.INFO)
    config = load_settings()
    db = Library(config.db_path)
    catalog = MusicCatalog(config.db_path)
    anime_catalog = AniListCatalog(config.db_path)
    screen_catalog = TmdbCatalog(config.db_path, config.tmdb_read_token or "")
    fingerprint_recognizer = AudioRecognition(
        config.db_path, config.acoustid_client_key or "",
    )
    bot = Bot(token=config.token)
    dp = Dispatcher()
    dp.include_router(create_router(
        db, config.owner_id, config.webapp_url,
        catalog=catalog, media_hosts=config.allowed_media_hosts,
        youtube_search_enabled=config.youtube_search_enabled,
        youtube_download_enabled=config.youtube_download_enabled,
        anime_catalog=anime_catalog,
        screen_catalog=screen_catalog,
        fingerprint_recognizer=fingerprint_recognizer,
        profile_github_repo=config.profile_github_repo,
        profile_github_token=config.profile_github_token,
    ))
    try:
        await await_telegram_ready(bot)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await catalog.close()
        await anime_catalog.close()
        await screen_catalog.close()
        await fingerprint_recognizer.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
