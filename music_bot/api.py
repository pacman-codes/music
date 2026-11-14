"""Private Mini App backend; no public streaming endpoints and no media on disk."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal

from aiogram import Bot
from aiogram.types import FSInputFile
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from music_bot.catalog import CatalogError, CatalogTrack, MusicCatalog, normalize, normalized_identity, valid_mbid
from music_bot.config import Settings, load_settings
from music_bot.importer import AudioIngestService, MediaImportError
from music_bot.main import deliver_queue
from music_bot.selection import select_wave_batch
from music_bot.storage import Library
from music_bot.webapp_auth import InvalidInitData, verify_init_data


class DirectImportRequest(BaseModel):
    url: str = Field(min_length=12, max_length=2048)


class PlaylistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class PlaylistAdd(BaseModel):
    track_id: int = Field(gt=0)


class RatingRequest(BaseModel):
    score: int = Field(ge=1, le=10, strict=True)


class PlaybackReport(BaseModel):
    event: Literal["skip", "complete"]


class QueueRequest(BaseModel):
    kind: Literal["wave", "discover", "liked", "playlist"]
    playlist_id: int | None = None


def create_app(config: Settings | None = None,
               catalog: MusicCatalog | None = None,
               importer: AudioIngestService | None = None) -> FastAPI:
    settings = config or load_settings()
    db = Library(settings.db_path)
    catalog_client = catalog or MusicCatalog(settings.db_path)
    app = FastAPI(
        title="music private Mini App",
        docs_url=None, redoc_url=None, openapi_url=None,
    )
    static = Path(__file__).resolve().parent.parent / "web"
    app.mount("/assets", StaticFiles(directory=static), name="assets")
    send_lock = asyncio.Lock()
    import_lock = asyncio.Lock()

    async def publish_audio(path: Path, title: str, artist: str) -> tuple[str, str]:
        # A *real* Telegram audio message ensures native playback.
        async with Bot(token=settings.token) as bot:
            message = await bot.send_audio(
                chat_id=settings.owner_id, audio=FSInputFile(path),
                title=title[:64], performer=artist[:64],
            )
        if message.audio is None:
            raise MediaImportError("Telegram не подтвердил аудиофайл")
        return message.audio.file_id, message.audio.file_unique_id

    ingest = importer or AudioIngestService(
        db, settings.allowed_media_hosts, publish_audio,
    )

    @app.on_event("shutdown")
    async def close_catalog():
        if catalog is None:
            await catalog_client.close()

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
        ratings = db.ratings()
        stats = db.playback_stats()
        return [{
            "id": t.id, "title": t.title, "artist": t.artist,
            "liked": t.id in liked, "rating": ratings.get(t.id),
            "reported_skips": stats.get(t.id, (0, 0))[0],
            "reported_completions": stats.get(t.id, (0, 0))[1],
        } for t in found]

    def catalog_result(recordings: list[CatalogTrack]) -> list[dict]:
        wanted = db.wishlist_ids()
        saved = {normalized_identity(t.title, t.artist): t.id
                 for t in db.all_tracks(1000)}
        linked = db.linked_candidate_tracks()
        output = []
        for t in recordings:
            body = t.public()
            body["in_wishlist"] = t.mbid in wanted
            body["local_track_id"] = linked.get(t.mbid) or saved.get(
                normalized_identity(t.title, t.artist)
            )
            output.append(body)
        return output

    @app.get("/api/discovery/search", dependencies=[auth])
    async def catalog_search(
        q: str = Query(min_length=2, max_length=100),
        limit: int = Query(default=15, ge=1, le=25),
        variants: bool = False,
    ):
        try:
            matches = await catalog_client.search(
                q, limit=limit, allow_variants=variants,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Некорректный поисковый запрос") from exc
        except CatalogError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        db.remember_candidates(matches)
        return {"results": catalog_result(matches), "type": "metadata_only"}

    @app.get("/api/discovery/radio", dependencies=[auth])
    async def catalog_artist_radio(
        artist: str = Query(default="", max_length=90),
        difficulty: Literal["easy", "medium", "hard"] = "medium",
        limit: int = Query(default=12, ge=1, le=20),
    ):
        seed_artist = artist.strip()
        if not seed_artist:
            # If no seed is entered, pick one of the user's own preferred
            # artists rather than claim to know their ListenBrainz account.
            favorites = db.playlist_tracks(db.liked_id(), 1000)
            ratings = db.ratings()
            rated = sorted(db.all_tracks(1000),
                           key=lambda t: ratings.get(t.id, 0), reverse=True)
            candidates = favorites or [t for t in rated if ratings.get(t.id, 0) >= 7]
            seed_artist = next(
                (t.artist for t in candidates
                 if t.artist.strip() and normalize(t.artist) != "неизвестный исполнитель"),
                "",
            )
        if not seed_artist:
            raise HTTPException(
                status_code=409,
                detail="Укажи исполнителя или добавь любимую музыку",
            )
        try:
            found = await catalog_client.artist_radio(
                seed_artist, difficulty=difficulty, limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Некорректный исполнитель") from exc
        except CatalogError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        db.remember_candidates(found)
        return {
            "artist_seed": seed_artist,
            "type": "metadata_only",
            "results": catalog_result(found),
        }

    @app.get("/api/discovery/wishlist", dependencies=[auth])
    async def wishlist():
        return {"results": [
            {**track, "playable": False}
            for track in db.wishlist(100)
        ]}

    @app.post("/api/discovery/wishlist/{mbid}", dependencies=[auth])
    async def wishlist_add(mbid: str):
        if not valid_mbid(mbid):
            raise HTTPException(422, detail="Некорректный MBID")
        try:
            changed = db.wishlist_add(mbid.lower())
        except ValueError as exc:
            raise HTTPException(
                404, detail="Сначала найди этот трек через музыкальный каталог"
            ) from exc
        return {"saved": changed}

    @app.delete("/api/discovery/wishlist/{mbid}", dependencies=[auth])
    async def wishlist_delete(mbid: str):
        if not valid_mbid(mbid):
            raise HTTPException(422, detail="Некорректный MBID")
        return {"removed": db.wishlist_remove(mbid.lower())}

    @app.get("/api/discovery/import-config", dependencies=[auth])
    async def import_config():
        return {
            "enabled": bool(settings.allowed_media_hosts),
            "formats": ["mp3", "m4a"],
            "max_bytes": 45 * 1024 * 1024,
            "note": "Only operator-allowlisted HTTPS sources; no YouTube resolver",
        }

    @app.post("/api/discovery/import/{mbid}", dependencies=[auth])
    async def import_audio(mbid: str, payload: DirectImportRequest):
        if not valid_mbid(mbid):
            raise HTTPException(422, detail="Некорректный MusicBrainz ID")
        if not settings.allowed_media_hosts and importer is None:
            raise HTTPException(
                409, detail="Импорт ещё не настроен на сервере",
            )
        async with import_lock:
            try:
                return await ingest.import_recording(mbid.lower(), payload.url)
            except MediaImportError as exc:
                raise HTTPException(422, detail=str(exc)) from exc

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

    @app.delete("/api/like/{track_id}", dependencies=[auth])
    async def unlike(track_id: int):
        return {"changed": db.unlike(track_id)}

    @app.post("/api/rating/{track_id}", dependencies=[auth])
    async def set_rating(track_id: int, payload: RatingRequest):
        try:
            db.set_rating(track_id, payload.score)
        except ValueError as exc:
            raise HTTPException(404, detail="Track not found") from exc
        return {"rating": payload.score}

    @app.delete("/api/rating/{track_id}", dependencies=[auth])
    async def clear_rating(track_id: int):
        return {"changed": db.clear_rating(track_id)}

    @app.post("/api/report/{track_id}", dependencies=[auth])
    async def report_playback(track_id: int, payload: PlaybackReport):
        # These are owner-entered notes, not native Telegram player telemetry.
        try:
            db.record_playback_event(track_id, payload.event, source="manual")
        except ValueError as exc:
            raise HTTPException(404, detail="Track not found") from exc
        return {"recorded": True, "source": "manual"}

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
            selected = select_wave_batch(db, mode, count=10)
            title = "Открытия" if mode == "discover" else "Моя волна"
        if not selected:
            raise HTTPException(409, detail="There are no tracks yet")
        # Radio kinds use the same internal mode names as the polling bot.
        # 'wave' is a public API alias, not a stored static queue type.
        source_kind = ("balanced" if request.kind == "wave" else request.kind)
        queue_id = db.create_queue(
            [t.id for t in selected], title, source_kind,
        )
        # App sends a real Telegram audio group into the owner's bot chat.
        # This is not browser audio playback.
        async with Bot(token=settings.token) as bot:
            await deliver_queue(bot, db, settings.owner_id, queue_id, send_lock)
        if db.peek_queue(queue_id).cursor == 0:
            raise HTTPException(502, detail="Telegram media delivery failed")
        return {"queue_id": queue_id, "sent": min(10, len(selected))}

    return app


