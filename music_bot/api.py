"""Private Mini App backend; no public streaming endpoints and no media on disk."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal

from aiogram import Bot
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from music_bot.config import Settings, load_settings
from music_bot.main import deliver_queue
from music_bot.recommend import wave
from music_bot.storage import Library
from music_bot.webapp_auth import InvalidInitData, verify_init_data


class PlaylistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class PlaylistAdd(BaseModel):
    track_id: int = Field(gt=0)


class QueueRequest(BaseModel):
    kind: Literal["wave", "discover", "liked", "playlist"]
    playlist_id: int | None = None


def create_app(config: Settings | None = None) -> FastAPI:
    settings = config or load_settings()
    db = Library(settings.db_path)
    app = FastAPI(
        title="music private Mini App",
        docs_url=None, redoc_url=None, openapi_url=None,
    )
    static = Path(__file__).resolve().parent.parent / "web"
    app.mount("/assets", StaticFiles(directory=static), name="assets")
    send_lock = asyncio.Lock()

    def owner(x_telegram_init_data: Annotated[str | None, Header()] = None):
        try:
            verify_init_data(
                x_telegram_init_data or "", settings.token, settings.owner_id,
            )
        except InvalidInitData as exc:
            # Never leak the raw initData or bot token in an error.
            raise HTTPException(status_code=401, detail="Telegram authorization failed") from exc
        return settings.owner_id

    auth = Depends(owner)

    @app.middleware("http")
    async def private_cache_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response

    @app.get("/")
    async def index():
        return FileResponse(static / "index.html")

    @app.get("/healthz")
    async def health():
        return {"ok": True}

    @app.get("/api/tracks", dependencies=[auth])
    async def tracks(q: str = Query(default="", max_length=100)):
        found = db.search_tracks(q, 80) if q else db.all_tracks(80)
        liked = db.liked_ids()
        return [{"id": t.id, "title": t.title, "artist": t.artist,
                 "liked": t.id in liked} for t in found]

    @app.get("/api/playlists", dependencies=[auth])
    async def playlists():
        return db.playlists()

    @app.get("/api/playlists/{playlist_id}/tracks", dependencies=[auth])
    async def playlist_tracks(playlist_id: int):
        if not db.get_playlist(playlist_id):
            raise HTTPException(status_code=404, detail="Playlist not found")
        return [{"id": t.id, "title": t.title, "artist": t.artist}
                for t in db.playlist_tracks(playlist_id, 100)]

    @app.post("/api/playlists", dependencies=[auth], status_code=201)
    async def create_playlist(payload: PlaylistCreate):
        import sqlite3
        try:
            playlist_id = db.create_playlist(payload.name)
        except (ValueError, sqlite3.IntegrityError) as exc:
            raise HTTPException(409, detail="Invalid or duplicate playlist name") from exc
        return {"id": playlist_id}

    @app.post("/api/like/{track_id}", dependencies=[auth])
    async def like(track_id: int):
        try:
            changed = db.like(track_id)
        except ValueError as exc:
            raise HTTPException(404, detail="Track not found") from exc
        return {"changed": changed}

    @app.post("/api/playlists/{playlist_id}/add", dependencies=[auth])
    async def add_to_playlist(playlist_id: int, payload: PlaylistAdd):
        try:
            changed = db.add_to(playlist_id, payload.track_id)
        except ValueError as exc:
            raise HTTPException(404, detail="Playlist or track not found") from exc
        return {"changed": changed}

    @app.post("/api/queue", dependencies=[auth])
    async def send_to_telegram(request: QueueRequest):
        if request.kind == "playlist":
            playlist = db.get_playlist(request.playlist_id)
            if not playlist:
                raise HTTPException(404, detail="Playlist not found")
            selected = db.playlist_tracks(request.playlist_id)
            title = playlist["name"]
        elif request.kind == "liked":
            selected = db.playlist_tracks(db.liked_id())
            title = "Мне нравится"
        else:
            mode = "discover" if request.kind == "discover" else "balanced"
            selected = wave(
                db.all_tracks(), db.liked_ids(), db.disliked_ids(),
                recent_ids=db.recent_ids(60), count=60, mode=mode,
            )
            title = "Открытия" if mode == "discover" else "Моя волна"
        if not selected:
            raise HTTPException(409, detail="There are no tracks yet")
        queue_id = db.create_queue(
            [t.id for t in selected], title, request.kind,
        )
        # App sends a real Telegram audio group into the owner's bot chat.
        # This is not browser audio playback.
        async with Bot(token=settings.token) as bot:
            await deliver_queue(bot, db, settings.owner_id, queue_id, send_lock)
        if db.peek_queue(queue_id).cursor == 0:
            raise HTTPException(502, detail="Telegram media delivery failed")
        return {"queue_id": queue_id, "sent": min(10, len(selected))}

    return app


