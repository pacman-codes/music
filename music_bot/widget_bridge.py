"""Transport-independent widget_query validation and dispatch.

Intentionally not registered with aiogram polling yet: as of Nov 14 2026,
Telegram disabled sendWidget for general production bots and aiogram's 10.4
handler wasn't released. Network updates must only arrive from Telegram API.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from music_bot.storage import Library
from music_bot.widgets import parse_rating_request


@dataclass(frozen=True)
class WidgetAnswer:
    query_id: str
    data: str


def handle_widget_query(
    raw: dict[str, Any], *, owner_id: int, db: Library
) -> WidgetAnswer | None:
    """Check Telegram-authenticated origin, then apply exactly one action.

    No shared-widget or inline contexts are accepted. Do NOT call this using
    untrusted HTTP input: the Telegram getUpdates/webhook transport is what
    authenticates `from.id` as the actual user.
    """
    if not isinstance(raw, dict):
        return None
    query = raw.get("widget_query")
    if not isinstance(query, dict):
        return None
    query_id = query.get("id")
    if not isinstance(query_id, str) or not 1 <= len(query_id) <= 128:
        return None

    sender = query.get("from")
    chat = query.get("chat")
    if not isinstance(sender, dict) or not isinstance(chat, dict):
        return WidgetAnswer(query_id, "Недоступно")
    if (type(sender.get("id")) is not int or sender["id"] != owner_id or
        type(chat.get("id")) is not int or chat["id"] != owner_id or
        chat.get("type") != "private" or
        type(query.get("message_id")) is not int or query["message_id"] <= 0 or
        "inline_message_id" in query or "widget_id" in query):
        # Static, unshared, owner-only widgets exclusively.
        return WidgetAnswer(query_id, "Недоступно")
    try:
        cmd = parse_rating_request(query.get("data"))
        reply = db.execute_widget_command(
            chat["id"], query["message_id"], query_id, cmd,
        )
    except ValueError:
        reply = "Недопустимое действие"
    return WidgetAnswer(query_id, reply)
