"""Optional TMDB movie/TV metadata catalog (not streaming or media links).

The TMDB Read Access Token is stored server-side in private environment only.
It is never returned to clients or included in URL query parameters.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

from music_bot.catalog import CatalogCache, MAX_RESPONSE_BYTES

TMDB = "https://api.themoviedb.org"
KINDS = frozenset(("movie", "tv"))


class TmdbError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScreenTitle:
    id: int
    media_type: str
    title: str
    original_title: str | None
    release_year: int | None
    description: str | None
    average_score: float | None

    def public(self) -> dict:
        return {**asdict(self), "playable": False}


def parse_screen_title(row: Any) -> ScreenTitle | None:
    if not isinstance(row, dict) or row.get("media_type") not in KINDS:
        return None
    media_type = row["media_type"]
    id_ = row.get("id")
    if type(id_) is not int or not 0 < id_ <= 2_147_483_647:
        return None
    name = row.get("title") if media_type == "movie" else row.get("name")
    original = row.get("original_title") if media_type == "movie" else row.get("original_name")
    if not isinstance(name, str) or not name.strip():
        return None
    release = row.get("release_date") if media_type == "movie" else row.get("first_air_date")
    year = None
    if isinstance(release, str) and len(release) >= 4 and release[:4].isdigit():
        parsed = int(release[:4])
        if 1900 <= parsed <= 2200:
            year = parsed
    about = row.get("overview")
    if not isinstance(about, str):
        about = None
    rating = row.get("vote_average")
    if type(rating) not in (int, float) or not 0 <= rating <= 10:
        rating = None
    if row.get("adult") is True:
        return None
    return ScreenTitle(id_, media_type, name.strip()[:220],
                       original[:220] if isinstance(original, str) else None,
                       year, about[:800] if about else None,
                       round(float(rating), 1) if rating is not None else None)


class TmdbCatalog:
    def __init__(self, database: Path | str, token: str,
                 client: httpx.AsyncClient | None = None):
        self.cache = CatalogCache(database)
        self.token = token.strip()
        self._owned = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=4.0),
            follow_redirects=False, trust_env=False,
        )

    async def close(self):
        if self._owned:
            await self.client.aclose()

    async def search(self, term: str, limit: int = 15) -> list[ScreenTitle]:
        if not self.token:
            raise TmdbError("Каталог фильмов пока не настроен")
        query = term.strip()
        if not 2 <= len(query) <= 100 or not 1 <= limit <= 20:
            raise ValueError("Search requires 2–100 characters and 1–20 results")
        key = json.dumps(["tmdb", query.casefold(), limit, "ru-RU"])
        cached = self.cache.get(key)
        if cached is None:
            # Cautious shared quota across private bot and Mini App processes.
            import asyncio
            delay = self.cache.reserve_request_slot("tmdb", interval=0.5)
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                async with self.client.stream(
                    "GET", TMDB + "/3/search/multi",
                    params={"query": query, "language": "ru-RU",
                            "page": 1, "include_adult": "false"},
                    headers={"Authorization": "Bearer " + self.token,
                             "Accept": "application/json"},
                    follow_redirects=False,
                ) as response:
                    if response.status_code in (401, 403):
                        raise TmdbError("Ключ TMDB недействителен или доступ запрещён")
                    if response.status_code == 429:
                        raise TmdbError("TMDB ограничил запросы, попробуй позже")
                    if response.status_code != 200:
                        raise TmdbError("TMDB временно недоступен")
                    if int(response.headers.get("content-length", "0")) > MAX_RESPONSE_BYTES:
                        raise TmdbError("Ответ TMDB слишком большой")
                    payload = bytearray()
                    async for chunk in response.aiter_bytes():
                        payload.extend(chunk)
                        if len(payload) > MAX_RESPONSE_BYTES:
                            raise TmdbError("Ответ TMDB слишком большой")
                    if "json" not in response.headers.get("content-type", ""):
                        raise TmdbError("TMDB вернул неожиданный формат")
                    cached = json.loads(payload)
            except (httpx.HTTPError, ValueError) as exc:
                raise TmdbError("Нет соединения с TMDB") from exc
            if not isinstance(cached, dict) or not isinstance(cached.get("results"), list):
                raise TmdbError("TMDB вернул некорректный список")
            self.cache.put(key, cached, ttl=86400)
        result = [movie for row in cached["results"]
                  if (movie := parse_screen_title(row)) is not None]
        return result[:limit]
