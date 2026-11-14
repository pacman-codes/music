"""Prototype Telegram HTML rating widget. NOT sent until query handling is integrated."""
from __future__ import annotations

import html
import re
import zlib
from dataclasses import dataclass


@dataclass(frozen=True)
class WidgetCommand:
    action: str
    score: int | None = None


def parse_rating_request(data: str) -> WidgetCommand:
    """Server must bind widget message ID to track ID; no track ID in request."""
    if not isinstance(data, str) or not 1 <= len(data.encode("utf-8")) <= 64:
        raise ValueError("Invalid request")
    if data in ("skip", "complete"):
        return WidgetCommand(data)
    found = re.fullmatch(r"rate:(10|[1-9])", data)
    if found:
        return WidgetCommand("rate", int(found.group(1)))
    raise ValueError("Unsupported widget request")


def rating_widget_html(title: str, artist: str, rating: int | None = None) -> str:
    """No remote scripts, secrets, URL handling, eval, or unescaped metadata."""
    if rating is not None and (type(rating) is not int or not 1 <= rating <= 10):
        raise ValueError("Rating out of range")
    buttons = "".join(f'<button data-v="rate:{n}">{n}</button>' for n in range(1,11))
    page = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<style>body{font:14px system-ui;padding:8px;color:var(--tg-theme-text-color)}'
        'h3{margin:0 0 4px;font-size:17px}small{color:var(--tg-theme-hint-color)}'
        '.scores{display:grid;grid-template-columns:repeat(5,1fr);gap:5px;margin:12px 0}'
        'button{padding:9px 0;border-radius:9px}'
        '.actions{display:flex;gap:5px}.actions button{flex:1}</style>'
        '</head><body>'
        f'<h3>{html.escape(title[:180], quote=True)}</h3>'
        f'<small>{html.escape(artist[:180], quote=True)}</small>'
        f'<p>Оценка: <b id="score">{rating if rating is not None else "—"}</b>/10</p>'
        f'<div class="scores">{buttons}</div>'
        '<div class="actions"><button data-v="skip">⏭ Пропустил</button>'
        '<button data-v="complete">✅ Дослушал</button></div>'
        '<div id="status">Ручные отметки, не статистика плеера.</div>'
        '<script>'
        'const st=document.getElementById("status");'
        'document.querySelectorAll("button").forEach(b=>{'
        'b.addEventListener("click",()=>{'
        'const v=b.getAttribute("data-v");st.textContent="Сохраняю…";'
        'TelegramWidget.request(v).then(r=>{'
        'st.textContent=r.result||"Готово";'
        'if(v.startsWith("rate:"))document.getElementById("score").textContent=v.slice(5);'
        '},e=>{st.textContent="Ошибка: "+e.code;});'
        '});'
        '});'
        '</script></body></html>'
    )
    raw = page.encode("utf-8")
    if len(raw) > 32768 or len(zlib.compress(raw, 9)) > 4096:
        raise ValueError("Widget exceeds Telegram size limits")
    return page
