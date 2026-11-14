"""Owner-only TMDB movie and TV series metadata search, no video streaming."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from music_bot.screen import TmdbCatalog, TmdbError
from music_bot.storage import Library

KINDS={"movie","tv"}
STATES={"plan":"planned","watch":"watching","done":"completed",
        "hold":"on_hold","drop":"dropped"}


def install_screen_handlers(router: Router, db: Library,
                            catalog: TmdbCatalog) -> None:
    """Registered under the parent router's private owner middleware."""

    @router.message(Command("film"))
    async def film(message: Message):
        text=(message.text or "").partition(" ")[2].strip()
        if not 2<=len(text)<=100:
            await message.answer("Пример: /film Интерстеллар")
            return
        try:
            found=await catalog.search(text,limit=10)
        except (TmdbError,ValueError):
            await message.answer(
                "TMDB недоступен или не настроен. Для него нужен "
                "отдельный TMDB_READ_TOKEN в приватном .env."
            )
            return
        if not found:
            await message.answer("Фильмов и сериалов не найдено.")
            return
        db.remember_screen_titles(found)
        info="🎬 Результаты TMDB (без видео):\n"
        info+="\n".join(
            f"{i}. {m.title} ({m.release_year or '?'}) "
            f"{'сериал' if m.media_type=='tv' else 'фильм'} "
            f"[{m.media_type}:{m.id}]"[:220]
            for i,m in enumerate(found,1)
        )
        info+="\nОтложить: /filmstatus movie|tv ID plan|watch|done|hold|drop"
        await message.answer(info[:3800])

    @router.message(Command("films"))
    async def films(message: Message):
        entries=db.screen_watchlist(40)
        if not entries:
            await message.answer("Список пуст. Поиск: /film Название")
            return
        text="🎬 Мои фильмы и сериалы:\n"+"\n".join(
            f"{v['media_type']}:{v['tmdb_id']} {v['title']} — "
            f"{v['watch_status']} "
            + (f"сезон {v['season']} серия {v['episode']}" if v["media_type"]=="tv" else "")
            + (f" · {v['score']}/10" if v["score"] is not None else "")
            for v in entries
        )
        await message.answer(text[:3800])

    @router.message(Command("filmstatus"))
    async def film_status(message: Message):
        p=(message.text or "").split()
        if (len(p)!=4 or p[1] not in KINDS or not p[2].isdigit()
                or p[3] not in STATES):
            await message.answer("Пример: /filmstatus movie 157336 done")
            return
        try:
            result=db.update_screen_watch(
                p[1],int(p[2]),watch_status=STATES[p[3]],
            )
        except ValueError as exc:
            await message.answer(f"Не удалось: {str(exc)[:140]}")
            return
        await message.answer(f"Статус: {result['watch_status']}")

    @router.message(Command("filmprogress"))
    async def film_progress(message: Message):
        p=(message.text or "").split()
        if len(p)!=4 or not all(n.isdigit() for n in p[1:]):
            await message.answer("Пример: /filmprogress TV_ID сезон серия")
            return
        entries=db.screen_watchlist(200)
        current=next((e for e in entries
            if e["media_type"]=="tv" and e["tmdb_id"]==int(p[1])),None)
        if current is None:
            await message.answer("Сначала добавь сериал через /filmstatus tv ID watch")
            return
        try:
            result=db.update_screen_watch(
                "tv",int(p[1]),watch_status=current["watch_status"],
                season=int(p[2]),episode=int(p[3]),score=current["score"],
            )
        except ValueError as exc:
            await message.answer(f"Не удалось: {str(exc)[:140]}")
            return
        await message.answer(
            f"Прогресс сериала: сезон {result['season']}, серия {result['episode']}"
        )

    @router.message(Command("filmrate"))
    async def film_rate(message: Message):
        p=(message.text or "").split()
        if len(p)!=4 or p[1] not in KINDS or not all(v.isdigit() for v in p[2:]):
            await message.answer("Пример: /filmrate movie 157336 9")
            return
        entries=db.screen_watchlist(200)
        current=next((e for e in entries if e["media_type"]==p[1]
            and e["tmdb_id"]==int(p[2])),None)
        if current is None:
            await message.answer("Сначала добавь фильм/сериал через /filmstatus")
            return
        try:
            result=db.update_screen_watch(
                p[1],int(p[2]),watch_status=current["watch_status"],
                season=current["season"],episode=current["episode"],
                score=int(p[3]),
            )
        except ValueError as exc:
            await message.answer(f"Не удалось: {str(exc)[:140]}")
            return
        await message.answer(f"Оценка: {result['score']}/10")
