"""Read-only smart playlists of actual library audio, calculated on demand."""
from __future__ import annotations

from dataclasses import dataclass

from music_bot.storage import Library, Track


@dataclass(frozen=True)
class SmartRule:
    title: str
    description: str
    where: str
    order: str


# Only fixed application-owned SQL fragments. No user text enters SQL syntax.
RULES = {
    "recent": SmartRule("🆕 Недавно добавленные", "Добавлено за последние 30 дней.",
        "t.added_at >= datetime('now','-30 days')", "t.added_at DESC,t.id DESC"),
    "top": SmartRule("⭐ Высоко оценённые", "Оценка 8–10.",
        "r.score >= 8", "r.score DESC,r.updated_at DESC,t.id DESC"),
    "forgotten": SmartRule("💎 Забытое избранное", "Избранное, не отправлявшееся 30 дней или ни разу.",
        """v.status='add' AND NOT EXISTS (
            SELECT 1 FROM sent_history h WHERE h.track_id=t.id
              AND h.sent_at >= datetime('now','-30 days'))""",
        "t.id DESC"),
    "unrated": SmartRule("🎯 Без оценки", "Нет числовой оценки 1–10.",
        "r.track_id IS NULL", "t.id DESC"),
    "unheard": SmartRule("📦 Ещё не отправлялись", "Нет записи об отправке в Telegram.",
        "NOT EXISTS (SELECT 1 FROM sent_history h WHERE h.track_id=t.id)",
        "t.id DESC"),
}

BASE = """ FROM tracks t
    LEFT JOIN track_ratings r ON r.track_id=t.id
    LEFT JOIN track_verdicts v ON v.track_id=t.id
    WHERE (v.status IS NULL OR v.status NOT IN ('later','never','interesting'))
      AND """


def smart_counts(db: Library) -> dict[str, int]:
    with db.con() as con:
        return {key: int(con.execute("SELECT COUNT(*)" + BASE + rule.where).fetchone()[0])
                for key, rule in RULES.items()}


def smart_page(db: Library, key: str, page: int = 0, *, size: int = 8):
    if key not in RULES or not 0 <= page <= 100000 or not 1 <= size <= 100:
        raise ValueError("Invalid smart playlist page")
    rule = RULES[key]
    with db.con() as con:
        con.execute("BEGIN")
        total = int(con.execute("SELECT COUNT(*)" + BASE + rule.where).fetchone()[0])
        pages = max(1, (total + size - 1) // size)
        page = min(page, pages - 1)
        tracks = [db.track(row) for row in con.execute(
            "SELECT t.*" + BASE + rule.where + " ORDER BY " + rule.order + " LIMIT ? OFFSET ?",
            (size, page * size))]
    return total, page, pages, tracks


def smart_tracks(db: Library, key: str, *, limit: int = 1001) -> list[Track]:
    if key not in RULES or not 1 <= limit <= 1001:
        raise ValueError("Invalid smart playlist")
    rule = RULES[key]
    with db.con() as con:
        return [db.track(row) for row in con.execute(
            "SELECT t.*" + BASE + rule.where + " ORDER BY " + rule.order + " LIMIT ?", (limit,))]
