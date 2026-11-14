"""Opt-in upload of a metadata-only profile to a VERIFIED private GitHub repo.

Uses a fine-grained Contents-write token provided through environment only.
Disabled by default. Absolutely never pushes to the public source repository.
"""
from __future__ import annotations

import base64
import re
from datetime import datetime
from typing import Any

import httpx

from music_bot.profile_export import profile_json_bytes

REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
PATH = "profiles/latest.json"
MAX_CONTENT_BYTES = 900_000


class ProfileSyncError(RuntimeError):
    """A safe user-facing error without credentials or response payloads."""


async def sync_private_profile(
    profile: dict[str, Any], *,
    repo: str, token: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, str]:
    """PUT latest profile ONLY after GitHub confirms the repo is private.

    No remote writes if metadata lookup, privacy verification or read fails.
    Existing file uses GitHub's optimistic blob SHA to avoid blind overwrites.
    """
    if not isinstance(repo, str) or not REPOSITORY_PATTERN.fullmatch(repo):
        raise ProfileSyncError("Неверный адрес приватного репозитория GitHub.")
    if repo.casefold() == "pacman-codes/music":
        raise ProfileSyncError("Публичный репозиторий music запрещён для личных данных.")
    if not isinstance(token, str) or not token.strip():
        raise ProfileSyncError("Не настроен GitHub токен доступа.")
    payload = profile_json_bytes(profile)
    if len(payload) > MAX_CONTENT_BYTES:
        raise ProfileSyncError(
            "Музыкальный профиль слишком большой для одного GitHub JSON. "
            "Используй /tasteexport."
        )
    if profile.get("scope") != "complete_music_library" or profile.get("schema_version") != 1:
        raise ProfileSyncError("Некорректный формат музыкального профиля.")

    root = f"https://api.github.com/repos/{repo}"
    headers = {
        "Authorization": f"Bearer {token.strip()}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    owned = client is None
    http = client or httpx.AsyncClient(
        timeout=httpx.Timeout(12.0, connect=4.0),
        follow_redirects=False, trust_env=False,
    )
    try:
        metadata = await http.get(root, headers=headers, follow_redirects=False)
        if metadata.status_code != 200:
            raise ProfileSyncError("Не удалось проверить права и приватность GitHub-репозитория.")
        try:
            info = metadata.json()
        except ValueError:
            raise ProfileSyncError("GitHub вернул некорректные сведения о репозитории.") from None
        if info.get("private") is not True or info.get("full_name", "").casefold() != repo.casefold():
            raise ProfileSyncError("GitHub не подтвердил приватность указанного репозитория.")
        if info.get("archived"):
            raise ProfileSyncError("Репозиторий архивирован и недоступен для записи.")
        url = root + "/contents/" + PATH
        existing = await http.get(url, headers=headers, follow_redirects=False)
        if existing.status_code == 200:
            try:
                data = existing.json()
            except ValueError:
                raise ProfileSyncError("Не удалось проверить текущий файл GitHub.") from None
            sha = data.get("sha")
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise ProfileSyncError("Не удалось получить версию GitHub-файла.")
        elif existing.status_code == 404:
            sha = None
        else:
            raise ProfileSyncError("Нет доступа к файлу в приватном репозитории.")
        body = {
            "message": "Update private music-taste snapshot",
            "content": base64.b64encode(payload).decode("ascii"),
        }
        if sha:
            body["sha"] = sha
        response = await http.put(
            url, headers=headers, json=body, follow_redirects=False,
        )
        if response.status_code not in (200, 201):
            raise ProfileSyncError("GitHub не принял обновление профиля.")
        try:
            updated = response.json()
        except ValueError:
            raise ProfileSyncError("GitHub не подтвердил обновление профиля.") from None
        commit_sha = updated.get("commit", {}).get("sha")
        if not isinstance(commit_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
            raise ProfileSyncError("Не удалось подтвердить GitHub-коммит.")
        return {"repo": repo, "path": PATH, "commit_sha": commit_sha}
    except httpx.HTTPError as exc:
        raise ProfileSyncError("Ошибка подключения к GitHub; личные данные не публиковались в этом запросе.") from None
    finally:
        if owned:
            await http.aclose()
