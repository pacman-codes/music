"""Owner-only Telegram bot: library, playlists and native audio queues."""
from __future__ import annotations

import asyncio
import logging
import sqlite3

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaAudio, Message, WebAppInfo,
)

from music_bot.anime import AniListCatalog
from music_bot.screen import TmdbCatalog
from music_bot.catalog import MusicCatalog
from music_bot.config import load_settings
from music_bot.telegram_anime import install_anime_handlers
from music_bot.telegram_screen import install_screen_handlers
from music_bot.telegram_catalog import install_catalog_handlers
from music_bot.telegram_import import install_import_handlers
from music_bot.telegram_youtube import install_youtube_handlers
from music_bot.selection import DYNAMIC_MODES, select_wave_batch
from music_bot.storage import Library, Track

logger = logging.getLogger(__name__)

HELP = """🎵 music — личный плеер

Отправь аудио, чтобы добавить трек в библиотеку.
/library — последние треки
/search Текст — поиск в своей библиотеке
/find Название — внешний поиск MusicBrainz
/radio Исполнитель — похожая музыка ListenBrainz
/radiotrack ID — радио по конкретной песне
/recommend — персональные открытия по твоим высоким оценкам
/identify ID — подобрать MusicBrainz ID для локального файла
/unmatch ID — убрать ошибочную ручную привязку
/saved — отложенные находки (пока без аудио)
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
/wave — обычная Волна
/discover — открытия среди сохранённых треков

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
        [button("🎵 Библиотека", "nav:library")],
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
        [button("⭐ Оценить 1–10", f"rateopen:{track_id}"),
         button("❤️ Нравится", f"like:{track_id}")],
        [button("➕ В плейлист", f"chooseadd:{track_id}"),
         button("🚫 Не предлагать", f"dislike:{track_id}")],
    ])


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
        button(f"⭐ {index + 1}. {track.title[:14]}", f"rateopen:{track.id}")
        for index, track in enumerate(tracks)
    ]
    rows = [choices[offset:offset + 2] for offset in range(0, len(choices), 2)]
    if has_more:
        if next_cursor is None or next_cursor <= 0:
            raise ValueError("Continuation button requires its expected cursor")
        rows.append([button("▶️ Ещё 10", f"more:{queue_id}:{next_cursor}")])
    rows.append([button("🌊 Новая волна", "nav:wave"),
                 button("📂 Плейлисты", "nav:playlists")])
    return keyboard(rows)


def parse_more_callback(data: str) -> tuple[int, int] | None:
    """Bind an old Telegram button to the exact queue position it was issued for."""
    parts = data.split(":")
    if (len(parts) != 3 or parts[0] != "more" or
            not parts[1].isdigit() or not parts[2].isdigit()):
        return None
    queue_id, cursor = int(parts[1]), int(parts[2])
    return (queue_id, cursor) if queue_id > 0 and cursor > 0 else None


async def deliver_queue(bot: Bot, db: Library, chat_id: int, queue_id: int,
                        send_lock: asyncio.Lock, *,
                        expected_cursor: int | None = None) -> None:
    """Serialize sends and only advance a queue after Telegram accepts its batch."""
    async with send_lock:
        try:
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
                fresh = select_wave_batch(db, mode)
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
            "\n⭐ Выбери песню для оценки 1–10. "
            "Пропуски и дослушивания отмечаются только вручную."
        )
        await bot.send_message(
            chat_id, notice,
            reply_markup=queue_feedback_keyboard(
                batch.tracks, queue_id, dynamic or bool(batch.remaining_after),
                next_cursor=batch.cursor + len(batch.tracks),
            ),
        )


def create_router(db: Library, owner_id: int, webapp_url: str | None = None,
                  catalog: MusicCatalog | None = None,
                  media_hosts: tuple[str, ...] = (),
                  youtube_search_enabled: bool = False,
                  youtube_download_enabled: bool = False,
                  anime_catalog: AniListCatalog | None = None,
                  screen_catalog: TmdbCatalog | None = None) -> Router:
    router = Router()
    router.message.middleware(OwnerOnly(owner_id))
    router.callback_query.middleware(OwnerOnly(owner_id))
    send_lock = asyncio.Lock()

    async def start_wave(bot: Bot, chat_id: int, mode: str = "balanced") -> None:
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

    async def start_playlist(bot: Bot, chat_id: int, playlist_id: int) -> None:
        playlist = db.get_playlist(playlist_id)
        if not playlist:
            await bot.send_message(chat_id, "Плейлист не найден.")
            return
        tracks = db.playlist_tracks(playlist_id)
        if not tracks:
            await bot.send_message(chat_id, "Плейлист пуст.")
            return
        queue_id = db.create_queue(
            [t.id for t in tracks], playlist["name"], "playlist",
        )
        await deliver_queue(bot, db, chat_id, queue_id, send_lock)

    async def show_playlists(message: Message) -> None:
        playlists = db.playlists()
        text = "\n".join(
            f"#{p['id']} · {p['name']} ({p['count']})" for p in playlists[:30]
        )
        rows = [
            [button(f"▶️ {p['name'][:40]}", f"playlist:{p['id']}")]
            for p in playlists[:20]
        ]
        await message.answer("📂 Плейлисты:\n" + text, reply_markup=keyboard(rows))

    async def show_library(message: Message) -> None:
        tracks = db.all_tracks(20)
        await message.answer(
            format_tracks(tracks, "🎵 Библиотека:") if tracks
            else "Библиотека пуста. Пришли аудио."
        )

    async def show_liked(message: Message) -> None:
        tracks = db.playlist_tracks(db.liked_id(), 30)
        rows = [[button("▶️ Слушать понравившиеся", f"playlist:{db.liked_id()}")]]
        await message.answer(
            format_tracks(tracks, "❤️ Мне нравится:") if tracks
            else "Список понравившихся пока пуст.",
            reply_markup=keyboard(rows) if tracks else None,
        )

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
        if not custom:
            await message.answer("Сначала создай подборку: /newplaylist Название")
            return
        rows = [
            [button(f"➕ {p['name'][:40]}", f"doadd:{p['id']}:{track_id}")]
            for p in custom
        ]
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
        if action == "wave":
            await start_wave(bot, message.chat.id)
        elif action == "discover":
            await start_wave(bot, message.chat.id, "discover")
        elif action == "liked":
            await show_liked(message)
        elif action == "playlists":
            await show_playlists(message)
        elif action == "library":
            await show_library(message)

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
            format_tracks(tracks, "🔎 Результаты:") if tracks else "Ничего не найдено."
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
        info = db.get_playlist(ids[0])
        if not info:
            await message.answer("Плейлист не найден")
            return
        tracks = db.playlist_tracks(ids[0], 25)
        await message.answer(
            format_tracks(tracks, f"📂 {info['name']}:") if tracks
            else f"Плейлист «{info['name']}» пуст.",
            reply_markup=keyboard([[button("▶️ Слушать", f"playlist:{ids[0]}")]])
            if tracks else None,
        )

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
    install_youtube_handlers(
        router, db, owner_id, search_enabled=youtube_search_enabled,
        download_enabled=youtube_download_enabled,
    )
    if anime_catalog is not None:
        install_anime_handlers(router, db, anime_catalog)
    if screen_catalog is not None:
        install_screen_handlers(router, db, screen_catalog)
    return router


async def main():
    logging.basicConfig(level=logging.INFO)
    config = load_settings()
    db = Library(config.db_path)
    catalog = MusicCatalog(config.db_path)
    anime_catalog = AniListCatalog(config.db_path)
    screen_catalog = TmdbCatalog(config.db_path, config.tmdb_read_token or "")
    bot = Bot(token=config.token)
    dp = Dispatcher()
    dp.include_router(create_router(
        db, config.owner_id, config.webapp_url,
        catalog=catalog, media_hosts=config.allowed_media_hosts,
        youtube_search_enabled=config.youtube_search_enabled,
        youtube_download_enabled=config.youtube_download_enabled,
        anime_catalog=anime_catalog,
        screen_catalog=screen_catalog,
    ))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await catalog.close()
        await anime_catalog.close()
        await screen_catalog.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
