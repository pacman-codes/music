"""AniList anime metadata search; no streams, playable URLs or unauthorized media.

Public GraphQL endpoint accepts read-only fixed queries with bound variables.
AniList docs currently note a temporary 30 requests/minute limit; SQLite
cache plus shared rate slots keep the personal bot under that limit.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

from music_bot.catalog import CatalogCache, MAX_RESPONSE_BYTES

ANILIST = "https://graphql.anilist.co"
SEARCH_QUERY = """
query ($search: String!, $perPage: Int!) {
  Page(page: 1, perPage: $perPage) {
    media(search: $search, type: ANIME, isAdult: false) {
      id idMal
      title { romaji english native }
      episodes format status seasonYear averageScore
    }
  }
}
"""
TITLE_QUERY = """
query ($id: Int!) {
  Media(id: $id, type: ANIME) {
    id idMal
    title { romaji english native }
    episodes format status seasonYear averageScore
  }
}
"""
MAX_ID = 2_147_483_647
MAX_EPISODES = 10000


class AniListError(RuntimeError):
    pass


@dataclass(frozen=True)
class AnimeTitle:
    id: int
    title: str
    english: str | None
    native: str | None
    episodes: int | None
    format: str | None
    release_status: str | None
    year: int | None
    average_score: int | None
    mal_id: int | None

    def public(self) -> dict[str, Any]:
        return {**asdict(self), "playable": False}


def safe_title(value: Any) -> str | None:
    return value[:200].strip() if isinstance(value, str) and value.strip() else None


def parse_anime(data: Any) -> AnimeTitle | None:
    if not isinstance(data, dict):
        return None
    anime_id = data.get("id")
    if type(anime_id) is not int or not 0 < anime_id <= MAX_ID:
        return None
    titles = data.get("title")
    if not isinstance(titles, dict):
        return None
    english = safe_title(titles.get("english"))
    romaji = safe_title(titles.get("romaji"))
    native = safe_title(titles.get("native"))
    title = english or romaji or native
    if not title:
        return None
    episodes = data.get("episodes")
    if type(episodes) is not int or not 0 < episodes <= MAX_EPISODES:
        episodes = None
    year = data.get("seasonYear")
    if type(year) is not int or not 1900 <= year <= 2200:
        year = None
    average = data.get("averageScore")
    if type(average) is not int or not 0 <= average <= 100:
        average = None
    mal_id = data.get("idMal")
    if type(mal_id) is not int or not 0 < mal_id <= MAX_ID:
        mal_id = None
    fmt = data.get("format")
    status = data.get("status")
    return AnimeTitle(
        anime_id, title, english, native, episodes,
        fmt if isinstance(fmt, str) else None,
        status if isinstance(status, str) else None,
        year, average, mal_id,
    )


class AniListCatalog:
    def __init__(self, database: str | Path,
                 client: httpx.AsyncClient | None = None):
        self.cache = CatalogCache(database)
        self._owned = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(8, connect=4),
            follow_redirects=False, trust_env=False,
        )

    async def close(self):
        if self._owned:
            await self.client.aclose()

    async def _query(self, query: str, variables: dict,
                     cache_key: str, ttl: int = 43200) -> Any:
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        # No arbitrary GraphQL fragments are accepted from the user.
        import asyncio
        wait = self.cache.reserve_request_slot("anilist", interval=2.1)
        if wait:
            await asyncio.sleep(wait)
        try:
            async with self.client.stream(
                "POST", ANILIST,
                json={"query": query, "variables": variables},
                headers={"Accept": "application/json",
                         "Content-Type": "application/json"},
                follow_redirects=False,
            ) as response:
                if response.status_code == 429:
                    raise AniListError("AniList ограничил запросы; попробуй позже")
                if response.status_code != 200:
                    raise AniListError("AniList сейчас недоступен")
                if int(response.headers.get("content-length", "0")) > MAX_RESPONSE_BYTES:
                    raise AniListError("Слишком большой ответ AniList")
                if "json" not in response.headers.get("content-type", ""):
                    raise AniListError("AniList вернул некорректный формат")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE_BYTES:
                        raise AniListError("Слишком большой ответ AniList")
                payload = json.loads(content)
        except (httpx.HTTPError, ValueError) as exc:
            raise AniListError("Не получилось обратиться к AniList") from exc
        if not isinstance(payload, dict) or payload.get("errors"):
            raise AniListError("AniList не смог обработать запрос")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise AniListError("AniList вернул пустые данные")
        self.cache.put(cache_key, data, ttl=ttl)
        return data

    async def search(self, text: str, limit: int = 12) -> list[AnimeTitle]:
        term = text.strip()
        if not 2 <= len(term) <= 100 or not 1 <= limit <= 20:
            raise ValueError("Anime search requires 2–100 characters and 1–20 results")
        key = json.dumps(["anilist", "search", term.casefold(), limit])
        data = await self._query(
            SEARCH_QUERY, {"search": term, "perPage": limit}, key,
        )
        page = data.get("Page")
        rows = page.get("media") if isinstance(page, dict) else None
        if not isinstance(rows, list):
            raise AniListError("AniList не вернул список аниме")
        return [item for row in rows if (item := parse_anime(row)) is not None]

    async def get(self, anime_id: int) -> AnimeTitle | None:
        if type(anime_id) is not int or not 0 < anime_id <= MAX_ID:
            raise ValueError("Invalid anime ID")
        data = await self._query(
            TITLE_QUERY, {"id": anime_id}, f"anilist:anime:{anime_id}",
            ttl=86400,
        )
        return parse_anime(data.get("Media"))
