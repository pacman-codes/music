"""Owner-only AniList anime catalog and watchlist via ordinary Telegram chat."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from music_bot.anime import AniListCatalog, AniListError
from music_bot.storage import Library

STATES = {
    "plan": "planned", "watch": "watching", "done": "completed",
    "hold": "on_hold", "drop": "dropped",
}
LABELS = {
    "planned": "Планирую", "watching": "Смотрю",
    "completed": "Просмотрено", "on_hold": "На паузе", "dropped": "Брошено",
}


def format_anime(entries) -> str:
    lines = ["🎞 AniList — найденные аниме (пока без видео):"]
    for e in entries[:8]:
        year = f" ({e.year})" if e.year else ""
        episodes = f" · {e.episodes} сер." if e.episodes else ""
        lines.append(f"#{e.id} {e.title}{year}{episodes}"[:220])
    lines.append("Чтобы добавить: /animestatus ID plan|watch|done|hold|drop")
    return "\n".join(lines)[:3800]


def install_anime_handlers(router: Router, db: Library,
                           catalog: AniListCatalog) -> None:
    """Parent router must already enforce OwnerOnly for private chats."""

    @router.message(Command("anime"))
    async def search(message: Message):
        name = (message.text or "").partition(" ")[2].strip()
        if not 2 <= len(name) <= 100:
            await message.answer("Пример: /anime Cowboy Bebop")
            return
        try:
            rows = await catalog.search(name, limit=8)
        except (AniListError, ValueError):
            await message.answer("AniList сейчас не отвечает.")
            return
        if not rows:
            await message.answer("Подходящее аниме не найдено.")
            return
        db.remember_anime(rows)
        await message.answer(format_anime(rows))

    @router.message(Command("animelist"))
    async def watchlist(message: Message):
        items = db.anime_list(40)
        if not items:
            await message.answer("Список аниме пуст. Используй /anime Название.")
            return
        text = "🎞 Мои аниме:\n" + "\n".join(
            f"#{entry['id']} {entry['title']} — "
            f"{LABELS[entry['watch_status']]} "
            f"{entry['progress']}/{entry['episodes'] or '?'}"
            + (f" · {entry['score']}/10" if entry["score"] else "")
            for entry in items
        )
        await message.answer(text[:3800])

    @router.message(Command("animestatus"))
    async def status(message: Message):
        words = (message.text or "").split()
        if len(words) != 3 or not words[1].isdigit() or words[2].lower() not in STATES:
            await message.answer("Пример: /animestatus 12345 watch")
            return
        try:
            entry = db.update_anime_watch(
                int(words[1]), watch_status=STATES[words[2].lower()]
            )
        except ValueError as exc:
            await message.answer(f"Не удалось изменить статус: {exc}")
            return
        await message.answer(f"Готово: {LABELS[entry['watch_status']]}")

    @router.message(Command("animeprogress"))
    async def progress(message: Message):
        words = (message.text or "").split()
        if len(words) != 3 or not words[1].isdigit() or not words[2].isdigit():
            await message.answer("Пример: /animeprogress 12345 7")
            return
        try:
            entry = db.update_anime_watch(int(words[1]), progress=int(words[2]))
        except ValueError as exc:
            await message.answer(f"Не удалось изменить прогресс: {exc}")
            return
        await message.answer(f"Прогресс: {entry['progress']} серий.")

    @router.message(Command("animerate"))
    async def rating(message: Message):
        words = (message.text or "").split()
        if len(words) != 3 or not words[1].isdigit() or not words[2].isdigit():
            await message.answer("Пример: /animerate 12345 9")
            return
        try:
            entry = db.update_anime_watch(int(words[1]), score=int(words[2]))
        except ValueError as exc:
            await message.answer(f"Не удалось оценить: {exc}")
            return
        await message.answer(f"Оценка аниме: {entry['score']}/10")
