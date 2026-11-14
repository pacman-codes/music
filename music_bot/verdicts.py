"""Five mutually exclusive, low-friction music decisions.

"Interesting but won't listen" is curiosity, NOT an artist dislike.
"Not now" is a neutral pause. Neither means a Telegram playback event.
"""
from __future__ import annotations

from urllib.parse import quote

LABELS = {
    "maybe": "🤔 Может добавлю",
    "add": "❤️ Добавлю",
    "never": "🚫 Никогда не буду слушать",
    "later": "⏳ Точно не сейчас",
    "interesting": "🧠 Интересно, но слушать не буду",
}
STATUSES = tuple(LABELS)


def valid_status(status: str) -> bool:
    return isinstance(status, str) and status in LABELS


def yandex_search_url(artist: str, title: str) -> str:
    """Plain HTTPS search URL; metadata isn't a playable Telegram asset."""
    return "https://music.yandex.ru/search?text=" + quote(
        f"{artist} {title}".strip(), safe=""
    )
