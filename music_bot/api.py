"""Private Mini App backend; no public streaming endpoints and no media on disk."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from music_bot.anime import AniListCatalog, AniListError
from music_bot.albums import (
    find_albums, album_tracks, valid_album_id, valid_itunes_id,
    album_cover_urls, cached_album_group,
)
from music_bot.telegram_youtube import saved_album_ids, available_album_ids, album_track_ids, album_missing_suggestions
from music_bot.catalog import CatalogError, CatalogTrack, MusicCatalog, normalize, normalized_identity, valid_mbid
from music_bot.config import Settings, load_settings
from music_bot.importer import AudioIngestService, MediaImportError
from music_bot.fingerprints import (
    AudioRecognition, RecognitionError, recognize_telegram_track,
)
from music_bot.main import deliver_queue, send_single_track
from music_bot.personalized import personalized_discovery
from music_bot.weekly import weekly_discovery
from music_bot.wrapped import wrapped_summary
from music_bot.selection import select_wave_batch
from music_bot.screen import TmdbCatalog, TmdbError
from music_bot.source_matching import suggest_sources
from music_bot.storage import Library
from music_bot.taste_sources import (
    TasteSourceError, parse_yandex_url, fetch_yandex_playlist, parse_youtube_export,
)
from music_bot.verdicts import LABELS, STATUSES, valid_status, yandex_search_url
from music_bot.webapp_auth import InvalidInitData, verify_init_data
from music_bot.youtube import (
    YouTubeError, YouTubeSource, YouTubeIngestService, validate_video_id,
)


class AlbumAudioLinkRequest(BaseModel):
    recording_id: str = Field(min_length=4, max_length=36)
    track_id: int = Field(gt=0)


class DirectImportRequest(BaseModel):
    url: str = Field(min_length=12, max_length=2048)


class YandexLikedRequest(BaseModel):
    url: str = Field(min_length=18, max_length=350)


class YoutubeLikedRequest(BaseModel):
    filename: str = Field(min_length=5, max_length=100)
    content: str = Field(min_length=1, max_length=1_500_000)


class YoutubeImportRequest(BaseModel):
    # Only explicit selection in /ytfor or Mini App may set this identifier.
    recording_mbid: str | None = Field(default=None, min_length=36, max_length=36)


class RecordingMatchRequest(BaseModel):
    mbid: str = Field(min_length=36, max_length=36)


class ScreenWatchRequest(BaseModel):
    watch_status: Literal["planned", "watching", "completed", "on_hold", "dropped"]
    season: int = Field(default=0, ge=0, le=1000, strict=True)
    episode: int = Field(default=0, ge=0, le=10000, strict=True)
    score: int | None = Field(default=None, ge=1, le=10, strict=True)


class AnimeWatchRequest(BaseModel):
    watch_status: Literal["planned", "watching", "completed", "on_hold", "dropped"]
    progress: int = Field(default=0, ge=0, le=10000, strict=True)
    score: int | None = Field(default=None, ge=1, le=10, strict=True)


class PlaylistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class PlaylistMove(BaseModel):
    delta: Literal[-1, 1]


class PlaylistPlay(BaseModel):
    shuffle: bool = False
    start_track_id: int | None = Field(default=None, gt=0, strict=True)


class PlaylistAdd(BaseModel):
    track_id: int = Field(gt=0)


class RatingRequest(BaseModel):
    score: int = Field(ge=1, le=10, strict=True)


class VerdictRequest(BaseModel):
    status: Literal["maybe", "add", "never", "later", "interesting"]


class PlaybackReport(BaseModel):
    event: Literal["skip", "complete"]


class WavePresetRequest(BaseModel):
    preset: Literal["balanced", "familiar", "explore"]


class QueueItemRequest(BaseModel):
    action: Literal["next", "append"]
    track_id: int = Field(ge=1, strict=True)
    expected_cursor: int | None = Field(default=None, ge=0, strict=True)


class QueueMoveRequest(BaseModel):
    position: int = Field(ge=0, strict=True)
    to_position: int = Field(ge=0, strict=True)
    expected_cursor: int | None = Field(default=None, ge=0, strict=True)


class QueueFiltersRequest(BaseModel):
    min_rating: int = Field(default=0, ge=0, le=10, strict=True)
    liked_only: bool = False
    no_repeat_today: bool = False


class QueueRequest(BaseModel):
    kind: Literal["wave", "discover", "liked", "playlist"]
    playlist_id: int | None = None


def create_app(config: Settings | None = None,
               catalog: MusicCatalog | None = None,
               importer: AudioIngestService | None = None,
               youtube_source: YouTubeSource | None = None,
               youtube_ingest: YouTubeIngestService | None = None,
               anime_catalog: AniListCatalog | None = None,
               screen_catalog: TmdbCatalog | None = None,
               recognizer: AudioRecognition | None = None) -> FastAPI:
    settings = config or load_settings()
    db = Library(settings.db_path)
    catalog_client = catalog or MusicCatalog(settings.db_path)
    anime_client = anime_catalog or AniListCatalog(settings.db_path)
    screen_client = screen_catalog or TmdbCatalog(
        settings.db_path, settings.tmdb_read_token or "",
    )
    recognition_client = recognizer or AudioRecognition(
        settings.db_path, settings.acoustid_client_key or "",
    )
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            if catalog is None:
                await catalog_client.close()
            if anime_catalog is None:
                await anime_client.close()
            if screen_catalog is None:
                await screen_client.close()
            if recognizer is None:
                await recognition_client.close()

    app = FastAPI(
        title="music private Mini App",
        docs_url=None, redoc_url=None, openapi_url=None,
        lifespan=lifespan,
    )
    static = Path(__file__).resolve().parent.parent / "web"
    app.mount("/assets", StaticFiles(directory=static), name="assets")
    send_lock = asyncio.Lock()
    import_lock = asyncio.Lock()

    async def publish_audio(path: Path, title: str, artist: str) -> tuple[str, str]:
        # A *real* Telegram audio message ensures native playback.
        try:
            async with Bot(token=settings.token) as bot:
                message = await bot.send_audio(
                    chat_id=settings.owner_id, audio=FSInputFile(path),
                    title=title[:64], performer=artist[:64],
                )
        except TelegramAPIError as exc:
            raise MediaImportError("Telegram не принял аудиофайл, попробуй позже") from exc
        if message.audio is None:
            raise MediaImportError("Telegram не подтвердил аудиофайл")
        return message.audio.file_id, message.audio.file_unique_id

    ingest = importer or AudioIngestService(
        db, settings.allowed_media_hosts, publish_audio,
    )
    yt_source = youtube_source or YouTubeSource()
    yt_ingest = youtube_ingest or YouTubeIngestService(db, publish_audio)

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
        links = db.recording_for_tracks()
        verdicts = db.track_verdicts()
        return [{
            "id": t.id, "title": t.title, "artist": t.artist,
            "liked": t.id in liked, "rating": ratings.get(t.id),
            "decision": verdicts.get(t.id),
            "reported_skips": stats.get(t.id, (0, 0))[0],
            "reported_completions": stats.get(t.id, (0, 0))[1],
            "recording": links.get(t.id),
        } for t in found]

    def catalog_result(recordings: list[CatalogTrack]) -> list[dict]:
        wanted = db.wishlist_ids()
        catalog_decisions = db.catalog_verdicts()
        saved = {normalized_identity(t.title, t.artist): t.id
                 for t in db.all_tracks(10000)}
        linked = db.resolved_catalog_tracks()
        output = []
        for t in recordings:
            body = t.public()
            body["in_wishlist"] = t.mbid in wanted
            body["decision"] = catalog_decisions.get(t.mbid)
            body["local_track_id"] = linked.get(t.mbid)
            body["possible_match_track_id"] = (
                None if body["local_track_id"] is not None
                else saved.get(normalized_identity(t.title, t.artist))
            )
            body["match_confirmed"] = body["local_track_id"] is not None
            output.append(body)
        return output

    @app.post("/api/tracks/{track_id}/play", dependencies=[auth])
    async def play_single_track(track_id: int):
        if db.get_track(track_id) is None:
            raise HTTPException(404, detail="Трек не найден")
        try:
            async with Bot(token=settings.token) as bot:
                track = await send_single_track(
                    bot, db, settings.owner_id, track_id,
                )
        except TelegramAPIError as exc:
            raise HTTPException(
                502, detail="Telegram не смог отправить эту песню"
            ) from exc
        return {"sent": True, "track_id": track.id, "native_player": True}

    @app.post("/api/tracks/{track_id}/apply-recording-tags", dependencies=[auth])
    async def apply_recording_tags(track_id: int):
        try:
            t = db.apply_confirmed_recording_tags(track_id)
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return {"track_id":t.id, "title":t.title,"artist":t.artist,
                "updated_local_metadata":True,
                "embedded_telegram_tags_changed":False}

    @app.get("/api/fingerprint/status", dependencies=[auth])
    async def fingerprint_status():
        return {"enabled": bool(settings.acoustid_client_key)}

    @app.post("/api/tracks/{track_id}/fingerprint", dependencies=[auth])
    async def fingerprint_track(track_id: int):
        if not settings.acoustid_client_key and recognizer is None:
            raise HTTPException(409, detail="AcoustID не настроен")
        if db.get_track(track_id) is None:
            raise HTTPException(404, detail="Трек не найден")
        # Serialization shares the existing global transfer lease so
        # fingerprint downloads don't collide with a large media import.
        lease = db.acquire_media_import("global", "media")
        if lease is None:
            raise HTTPException(409, detail="Сервер занят загрузкой другого файла")
        try:
            async with Bot(token=settings.token) as bot:
                try:
                    return await recognize_telegram_track(
                        db, bot, recognition_client, track_id,
                    )
                except RecognitionError as exc:
                    raise HTTPException(422, detail=str(exc)) from exc
        finally:
            db.release_media_import("global", "media", lease)

    @app.get("/api/tracks/{track_id}/recording", dependencies=[auth])
    async def track_recording(track_id: int):
        if db.get_track(track_id) is None:
            raise HTTPException(404, detail="Трек не найден")
        return {"recording": db.recording_for_track(track_id)}

    @app.get("/api/tracks/{track_id}/match-suggestions", dependencies=[auth])
    async def track_match_suggestions(track_id: int):
        track = db.get_track(track_id)
        if track is None:
            raise HTTPException(404, detail="Трек не найден")
        term = f"{track.artist} {track.title}"[:100]
        if len(term.strip()) < 2:
            return {"results": []}
        try:
            results = await catalog_client.search(
                term, limit=8, allow_variants=True
            )
        except (ValueError, CatalogError) as exc:
            raise HTTPException(502, detail="MusicBrainz сейчас недоступен") from exc
        db.remember_candidates(results)
        # Suggestions are NOT automatically linked. Owner confirmation required.
        return {"results": catalog_result(results)}

    @app.post("/api/tracks/{track_id}/recording", dependencies=[auth])
    async def confirm_track_recording(track_id: int, payload: RecordingMatchRequest):
        if not valid_mbid(payload.mbid):
            raise HTTPException(422, detail="Некорректный MusicBrainz MBID")
        try:
            record = db.confirm_recording_match(track_id, payload.mbid.lower())
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return {"confirmed": True, "recording": record}

    @app.delete("/api/tracks/{track_id}/recording", dependencies=[auth])
    async def remove_track_recording(track_id: int):
        if db.get_track(track_id) is None:
            raise HTTPException(404, detail="Трек не найден")
        return {"removed": db.unlink_recording_match(track_id)}

    @app.get("/api/tracks/{track_id}/radio", dependencies=[auth])
    async def track_radio(
        track_id: int,
        difficulty: Literal["easy", "medium", "hard"] = "medium",
        limit: int = Query(default=12, ge=1, le=20),
    ):
        track = db.get_track(track_id)
        if track is None:
            raise HTTPException(404, detail="Трек не найден")
        linked = db.recording_for_track(track_id)
        seed_mbid = linked.get("artist_mbid") if linked else None
        try:
            if seed_mbid and valid_mbid(seed_mbid):
                rows = await catalog_client.artist_radio_by_id(
                    seed_mbid, difficulty=difficulty, limit=limit
                )
            else:
                rows = await catalog_client.artist_radio(
                    track.artist, difficulty=difficulty, limit=limit
                )
        except (CatalogError, ValueError) as exc:
            raise HTTPException(502, detail="Радио по треку пока недоступно") from exc
        db.remember_candidates(rows)
        already = db.confirmed_recording_ids()
        own_names = {normalized_identity(t.title, t.artist)
                     for t in db.all_tracks(1000)}
        fresh = [r for r in rows if r.mbid not in already and
                 normalized_identity(r.title, r.artist) not in own_names]
        return {
            "seed_track_id": track.id,
            "seed_track": {"title": track.title, "artist": track.artist},
            "used_confirmed_artist_id": bool(seed_mbid and valid_mbid(seed_mbid)),
            "type": "metadata_only",
            "results": catalog_result(fresh),
        }

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

    @app.get("/api/discovery/weekly", dependencies=[auth])
    async def weekly_recommendations():
        result = await weekly_discovery(db, catalog_client, limit=10)
        candidates = [
            CatalogTrack(
                t["mbid"], t["title"], t["artist"],
                t.get("artist_mbid"), t.get("duration_ms"),
                t.get("variant", False),
            )
            for t in result["tracks"]
        ]
        db.remember_candidates(candidates)
        result["tracks"] = catalog_result(candidates)
        return result

    @app.get("/api/discovery/for-you", dependencies=[auth])
    async def catalog_for_you(
        difficulty: Literal["easy", "medium", "hard"] = "medium",
        limit: int = Query(default=15, ge=1, le=20),
    ):
        try:
            seeds, songs = await personalized_discovery(
                db, catalog_client, difficulty=difficulty, limit=limit,
            )
        except CatalogError as exc:
            raise HTTPException(502, detail=str(exc)) from exc
        if not seeds:
            raise HTTPException(
                409, detail="Поставь 7–10 любимым песням или добавь их в избранное"
            )
        db.remember_candidates(songs)
        return {
            "seed_artists": seeds, "results": catalog_result(songs),
            "type": "metadata_only",
        }

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
        linked = db.resolved_catalog_tracks()
        saved = {normalized_identity(t.title, t.artist): t.id
                 for t in db.all_tracks(1000)}
        return {"results": [
            {
                **track,
                "playable": False,
                "in_wishlist": True,
                "local_track_id": linked.get(track["mbid"]),
                "possible_match_track_id": (
                    None if linked.get(track["mbid"]) is not None
                    else saved.get(normalized_identity(track["title"], track["artist"]))
                ),
                "match_confirmed": track["mbid"] in linked,
            }
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

    @app.get("/api/discovery/{mbid}/youtube-suggestions", dependencies=[auth])
    async def youtube_suggestions_for_recording(mbid: str):
        if not settings.youtube_search_enabled:
            raise HTTPException(409, detail="YouTube-поиск отключён")
        if not valid_mbid(mbid):
            raise HTTPException(422, detail="Некорректный MusicBrainz ID")
        recording = db.get_candidate(mbid.lower())
        if recording is None:
            raise HTTPException(404, detail="Сначала найди запись в каталоге")
        try:
            candidates = await yt_source.search(
                f"{recording['artist']} {recording['title']}"[:90],
                limit=8,
            )
        except (ValueError, YouTubeError) as exc:
            raise HTTPException(
                502, detail="Пока не удалось получить предложения YouTube"
            ) from exc
        db.remember_youtube_videos(candidates)
        matches = suggest_sources(recording, candidates, limit=6)
        return {
            "recording": {
                "mbid": recording["mbid"], "title": recording["title"],
                "artist": recording["artist"],
            },
            "suggestions": [
                {**suggestion.public(), "recording_mbid": recording["mbid"]}
                for suggestion in matches
            ],
            "auto_imported": False,
        }

    @app.get("/api/screen/status", dependencies=[auth])
    async def screen_catalog_status():
        return {"enabled": bool(settings.tmdb_read_token)}

    @app.get("/api/screen/search", dependencies=[auth])
    async def search_screen(
        q: str = Query(min_length=2, max_length=100),
        limit: int = Query(default=15, ge=1, le=20),
    ):
        if not settings.tmdb_read_token and screen_catalog is None:
            raise HTTPException(409, detail="Каталог TMDB пока не настроен")
        try:
            movies = await screen_client.search(q, limit=limit)
        except ValueError as exc:
            raise HTTPException(422, detail="Некорректный запрос") from exc
        except TmdbError as exc:
            raise HTTPException(502, detail=str(exc)) from exc
        db.remember_screen_titles(movies)
        return {"results": [movie.public() for movie in movies],
                "type": "metadata_only"}

    @app.get("/api/screen/watchlist", dependencies=[auth])
    async def screen_watchlist():
        return {"results": [{**item,"playable":False}
                            for item in db.screen_watchlist()]}

    @app.post("/api/screen/watchlist/{kind}/{tmdb_id}", dependencies=[auth])
    async def screen_watch_update(
        kind: Literal["movie","tv"], tmdb_id: int,
        body: ScreenWatchRequest,
    ):
        try:
            return db.update_screen_watch(
                kind,tmdb_id,watch_status=body.watch_status,
                season=body.season,episode=body.episode,score=body.score,
            )
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc

    @app.delete("/api/screen/watchlist/{kind}/{tmdb_id}", dependencies=[auth])
    async def screen_watch_delete(kind: Literal["movie","tv"], tmdb_id: int):
        return {"removed": db.remove_screen_watch(kind,tmdb_id)}

    @app.get("/api/anime/search", dependencies=[auth])
    async def anime_search(
        q: str = Query(min_length=2, max_length=100),
        limit: int = Query(default=12, ge=1, le=20),
    ):
        try:
            matches = await anime_client.search(q, limit=limit)
        except ValueError as exc:
            raise HTTPException(422, detail="Некорректный запрос") from exc
        except AniListError as exc:
            raise HTTPException(502, detail=str(exc)) from exc
        db.remember_anime(matches)
        return {"type": "metadata_only",
                "results": [entry.public() for entry in matches]}

    @app.get("/api/anime/watchlist", dependencies=[auth])
    async def anime_watchlist():
        return {"results": [
            {**record, "playable": False} for record in db.anime_list()
        ]}

    @app.post("/api/anime/watchlist/{anime_id}", dependencies=[auth])
    async def anime_watch_update(anime_id: int, payload: AnimeWatchRequest):
        try:
            return db.update_anime_watch(
                anime_id, watch_status=payload.watch_status,
                progress=payload.progress, score=payload.score,
            )
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc

    @app.delete("/api/anime/watchlist/{anime_id}", dependencies=[auth])
    async def anime_watch_remove(anime_id: int):
        return {"removed": db.remove_anime_watch(anime_id)}

    @app.get("/api/youtube/status", dependencies=[auth])
    async def youtube_status():
        return {
            "search_enabled": settings.youtube_search_enabled,
            "download_enabled": settings.youtube_download_enabled,
            "max_duration_seconds": 1200,
            "max_audio_bytes": 45 * 1024 * 1024,
        }

    @app.get("/api/youtube/search", dependencies=[auth])
    async def youtube_search(q: str = Query(min_length=2, max_length=90)):
        if not settings.youtube_search_enabled:
            raise HTTPException(409, detail="YouTube-поиск отключён")
        try:
            videos = await yt_source.search(q, limit=6)
        except (YouTubeError, ValueError) as exc:
            raise HTTPException(502, detail="YouTube-поиск временно недоступен") from exc
        db.remember_youtube_videos(videos)
        return {
            "type": "metadata_only",
            "results": [{**v.public(),
                "track_id": (
                    db.get_youtube_video(v.video_id) or {}
                ).get("track_id")} for v in videos],
        }

    @app.post("/api/youtube/import/{video_id}", dependencies=[auth])
    async def youtube_import(
        video_id: str, payload: YoutubeImportRequest | None = None
    ):
        if not settings.youtube_download_enabled:
            raise HTTPException(409, detail="Загрузка YouTube отключена")
        try:
            validate_video_id(video_id)
        except YouTubeError as exc:
            raise HTTPException(422, detail="Некорректный YouTube ID") from exc
        async with import_lock:
            try:
                mbid = payload.recording_mbid if payload is not None else None
                if mbid is not None and not valid_mbid(mbid):
                    raise HTTPException(422, detail="Некорректный MusicBrainz ID")
                if mbid is None:
                    result = await yt_ingest.import_video(video_id)
                else:
                    result = await yt_ingest.import_video(
                        video_id, recording_mbid=mbid,
                    )
            except YouTubeError as exc:
                raise HTTPException(422, detail=str(exc)) from exc
            except MediaImportError as exc:
                raise HTTPException(502, detail=str(exc)) from exc
            if result["reused"]:
                track = db.get_track(result["track_id"])
                if track:
                    try:
                        async with Bot(token=settings.token) as bot:
                            await bot.send_audio(settings.owner_id, audio=track.file_id)
                    except TelegramAPIError as exc:
                        raise HTTPException(
                            502, detail="Не удалось повторно отправить аудио в Telegram"
                        ) from exc
        return result

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

    @app.get("/api/sources", dependencies=[auth])
    async def taste_sources():
        return db.taste_sources()

    @app.get("/api/sources/{provider}/tracks", dependencies=[auth])
    async def taste_tracks(
        provider: str, offset: int = Query(default=0, ge=0, le=1000000),
        limit: int = Query(default=20, ge=1, le=100),
    ):
        if provider not in {"yandex", "youtube_music"}:
            raise HTTPException(404, detail="Unknown music source")
        return {"provider": provider, "offset": offset, "limit": limit,
                "total": next((s["count"] for s in db.taste_sources()
                               if s["provider"] == provider), 0),
                "items": db.taste_tracks(provider, limit=limit, offset=offset)}

    @app.post("/api/sources/yandex/sync", dependencies=[auth])
    async def taste_yandex_sync(payload: YandexLikedRequest):
        try:
            parse_yandex_url(payload.url)
            lease = db.acquire_media_import("taste", "yandex", ttl=150)
            if lease is None:
                raise HTTPException(409, detail="Синхронизация уже выполняется")
            try:
                tracks = await fetch_yandex_playlist(payload.url)
                return db.sync_taste_source(
                    "yandex", tracks, public_url=payload.url, complete=True)
            finally:
                db.release_media_import("taste", "yandex", lease)
        except TasteSourceError as exc:
            raise HTTPException(422, detail=str(exc)) from exc

    @app.post("/api/sources/yandex/refresh", dependencies=[auth])
    async def taste_yandex_refresh():
        row = next((s for s in db.taste_sources()
                    if s["provider"] == "yandex"), None)
        if not row or not row["public_url"]:
            raise HTTPException(409, detail="Сначала добавь ссылку на плейлист")
        return await taste_yandex_sync(YandexLikedRequest(url=row["public_url"]))

    @app.post("/api/sources/youtube_music/import", dependencies=[auth])
    async def taste_youtube_import(payload: YoutubeLikedRequest):
        lease = db.acquire_media_import("taste", "youtube_music", ttl=150)
        if lease is None:
            raise HTTPException(409, detail="Импорт уже выполняется")
        try:
            songs, complete = parse_youtube_export(
                payload.content.encode("utf-8"), payload.filename)
            return db.sync_taste_source("youtube_music", songs, complete=complete)
        except (ValueError, TasteSourceError) as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        finally:
            db.release_media_import("taste", "youtube_music", lease)

    @app.get("/api/decisions", dependencies=[auth])
    async def decision_categories():
        counts = db.verdict_counts()
        return [
            {"status": status, "label": LABELS[status], "count": counts[status]}
            for status in STATUSES
        ]

    @app.get("/api/decisions/{status}", dependencies=[auth])
    async def decision_tracks(
        status: str, offset: int = Query(default=0, ge=0, le=1000000),
        limit: int = Query(default=30, ge=1, le=100),
    ):
        if not valid_status(status):
            raise HTTPException(404, detail="Unknown decision category")
        total = db.verdict_counts()[status]
        rows = db.verdict_items(status, limit=limit, offset=offset)
        return {
            "status": status, "label": LABELS[status],
            "total": total, "offset": offset, "limit": limit,
            "items": [
                {
                    **item,
                    "playable": item["track_id"] is not None,
                    "yandex_search_url": yandex_search_url(item["artist"], item["title"]),
                }
                for item in rows
            ],
        }

    @app.post("/api/decisions/local/{track_id}", dependencies=[auth])
    async def decide_local(track_id: int, payload: VerdictRequest):
        try:
            db.set_track_verdict(track_id, payload.status)
        except ValueError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        return {"track_id": track_id, "status": payload.status}

    @app.post("/api/decisions/catalog/{mbid}", dependencies=[auth])
    async def decide_catalog(mbid: str, payload: VerdictRequest):
        if not valid_mbid(mbid):
            raise HTTPException(422, detail="Invalid recording MBID")
        try:
            db.set_catalog_verdict(mbid.lower(), payload.status)
        except ValueError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        return {"mbid": mbid.lower(), "status": payload.status}

    @app.post("/api/decisions/{status}/play", dependencies=[auth])
    async def play_decision_list(status: str):
        if not valid_status(status):
            raise HTTPException(404, detail="Unknown decision category")
        playable_ids = db.verdict_playable_ids(status)
        if not playable_ids:
            raise HTTPException(409, detail="Нет загруженных аудиофайлов")
        queue_id = db.create_queue(playable_ids, LABELS[status], "decision")
        async with Bot(token=settings.token) as bot:
            await deliver_queue(bot, db, settings.owner_id, queue_id, send_lock)
        if db.peek_queue(queue_id).cursor == 0:
            raise HTTPException(502, detail="Telegram media delivery failed")
        return {"queue_id": queue_id, "sent": min(10, len(playable_ids))}

    def album_id(value: str) -> str:
        if not valid_album_id(value):
            raise HTTPException(422, detail="Некорректный идентификатор альбома")
        return value.lower()

    async def load_album(value: str):
        ident = album_id(value)
        try:
            title, artist, rows = await album_tracks(catalog_client, ident)
        except (CatalogError, ValueError) as exc:
            raise HTTPException(502, detail="Не удалось получить трек-лист альбома") from exc
        return ident, title, artist, rows

    @app.get("/api/albums", dependencies=[auth])
    async def saved_album_list(offset: int = Query(0, ge=0, le=100000)):
        rows = db.saved_albums(offset=offset, limit=21)
        return {"results": [
            {**row, "cover_urls": album_cover_urls(
                row["mbid"], cached_album_group(catalog_client, row["mbid"]))}
            for row in rows[:20]], "has_more": len(rows) > 20}

    @app.get("/api/albums/search", dependencies=[auth])
    async def search_album(q: str = Query(min_length=2, max_length=90)):
        try:
            rows = await find_albums(catalog_client, q, exact=False)
        except ValueError as exc:
            raise HTTPException(422, detail="Некорректный запрос") from exc
        except CatalogError as exc:
            raise HTTPException(502, detail="Каталоги альбомов недоступны") from exc
        return {"results": [{**asdict(row), "saved": db.get_saved_album(row.mbid) is not None,
                             "cover_urls": album_cover_urls(row.mbid, row.group)}
                            for row in rows]}

    @app.get("/api/albums/{release_id}", dependencies=[auth])
    async def album_detail(release_id: str):
        ident, title, artist, rows = await load_album(release_id)
        linked = album_track_ids(db, rows)
        manual = db.album_audio_links()
        tracks = []
        for row, local in zip(rows, linked):
            tracks.append({"title": row.title, "artist": row.artist,
                           "recording_id": row.mbid, "album_linked": row.mbid in manual,
                           "duration_ms": row.duration_ms, "local_track_id": local})
        return {"mbid": ident, "title": title, "artist": artist,
                "provider": "itunes" if valid_itunes_id(ident) else "musicbrainz",
                "cover_urls": album_cover_urls(ident, cached_album_group(catalog_client, ident)),
                "saved": db.get_saved_album(ident) is not None,
                "track_count": len(rows), "tracks": tracks,
                "available_count": sum(ident is not None for ident in linked),
                "queue_count": len({ident for ident in linked if ident is not None}),
                "playable": bool(saved_album_ids(db, rows))}

    @app.post("/api/albums/{release_id}", dependencies=[auth])
    async def add_album(release_id: str):
        ident, title, artist, rows = await load_album(release_id)
        return {"changed": db.save_album(ident, title, artist, len(rows)), "saved": True}

    @app.delete("/api/albums/{release_id}", dependencies=[auth])
    async def delete_album(release_id: str):
        return {"removed": db.remove_saved_album(album_id(release_id))}

    @app.post("/api/albums/{release_id}/play", dependencies=[auth])
    async def play_album(release_id: str, partial: bool = False):
        ident, title, artist, rows = await load_album(release_id)
        ids = available_album_ids(db, rows) if partial else saved_album_ids(db, rows)
        if not ids:
            raise HTTPException(409, detail="Не все треки альбома сохранены как подтверждённое аудио")
        label = f"{artist} — {title}" + (" (доступные)" if partial else "")
        queue_id = db.create_queue(ids, label, "playlist")
        async with Bot(token=settings.token) as bot:
            await deliver_queue(bot, db, settings.owner_id, queue_id, send_lock)
        if db.peek_queue(queue_id).cursor == 0:
            raise HTTPException(502, detail="Telegram не смог отправить альбом")
        return {"queue_id": queue_id, "sent": min(10, len(ids))}

    async def album_recording_at(release_id: str, position: int, expected: str):
        _, _, _, rows = await load_album(release_id)
        if not 0 <= position < len(rows):
            raise HTTPException(404, detail="Трек альбома не найден")
        track = rows[position]
        if track.mbid != expected:
            raise HTTPException(409, detail="Трек-лист изменился. Обнови альбом.")
        return track

    @app.post("/api/albums/{release_id}/tracks/{position}/library-link", dependencies=[auth])
    async def link_album_audio(release_id: str, position: int, payload: AlbumAudioLinkRequest):
        track = await album_recording_at(release_id, position, payload.recording_id)
        if db.get_track(payload.track_id) is None:
            raise HTTPException(404, detail="Аудио не найдено в медиатеке")
        current = album_track_ids(db, [track])[0]
        if current is not None and current != payload.track_id:
            raise HTTPException(409, detail="Запись уже связана с другим аудио")
        if current == payload.track_id:
            return {"changed": False, "local_track_id": current}
        try:
            changed = db.confirm_album_audio(track.mbid, payload.track_id)
        except ValueError as exc:
            raise HTTPException(409, detail="Не удалось связать аудио. Обнови альбом.") from exc
        return {"changed": changed, "local_track_id": payload.track_id}

    @app.delete("/api/albums/{release_id}/tracks/{position}/library-link", dependencies=[auth])
    async def unlink_album_audio(release_id: str, position: int,
                                 recording_id: str = Query(min_length=4, max_length=36)):
        track = await album_recording_at(release_id, position, recording_id)
        return {"removed": db.unlink_album_audio(track.mbid)}

    @app.post("/api/albums/{release_id}/missing-suggestions", dependencies=[auth])
    async def missing_album_suggestions(release_id: str, start: int = Query(0, ge=0, le=100)):
        if not settings.youtube_search_enabled:
            raise HTTPException(409, detail="YouTube-поиск отключён")
        _, _, _, rows = await load_album(release_id)
        try:
            return await album_missing_suggestions(db, yt_source, rows, start)
        except ValueError as exc:
            raise HTTPException(422, detail="Неверная позиция") from exc
        except YouTubeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @app.get("/api/albums/{release_id}/tracks/{position}/youtube-suggestions", dependencies=[auth])
    async def album_track_suggestions(release_id: str, position: int):
        if not settings.youtube_search_enabled:
            raise HTTPException(409, detail="YouTube-поиск отключён")
        _, _, _, rows = await load_album(release_id)
        if not 0 <= position < len(rows):
            raise HTTPException(404, detail="Трек альбома не найден")
        track = rows[position]
        recording = {"title": track.title, "artist": track.artist,
                     "duration_ms": track.duration_ms}
        recording_id = track.mbid if valid_mbid(track.mbid) else None
        if recording_id:
            db.remember_candidates([track])
        try:
            candidates = await yt_source.search(
                f"{track.artist} {track.title}"[:90], limit=8)
        except (ValueError, YouTubeError) as exc:
            raise HTTPException(502, detail="Поиск записи временно недоступен") from exc
        db.remember_youtube_videos(candidates)
        return {"suggestions": [
            {**suggestion.public(), "recording_mbid": recording_id}
            for suggestion in suggest_sources(recording, candidates, limit=6)
        ], "auto_imported": False}

    @app.get("/api/playlists", dependencies=[auth])
    async def playlists():
        return db.playlists()

    @app.get("/api/playlists/{playlist_id}/tracks", dependencies=[auth])
    async def playlist_tracks(playlist_id: int):
        if not db.get_playlist(playlist_id):
            raise HTTPException(status_code=404, detail="Playlist not found")
        return [{"id": t.id, "title": t.title, "artist": t.artist}
                for t in db.playlist_tracks(playlist_id, 100)]

    @app.get("/api/playlists/{playlist_id}", dependencies=[auth])
    async def playlist_detail(playlist_id: int, page: int = Query(default=0, ge=0, le=100000)):
        info = db.get_playlist(playlist_id)
        if not info:
            raise HTTPException(404, detail="Плейлист не найден")
        total, page, pages, rows = db.playlist_page(playlist_id, page, 20)
        return {"id": playlist_id, "name": info["name"], "kind": info["kind"],
                "total": total, "page": page, "pages": pages,
                "tracks": [{"id": t.id, "title": t.title, "artist": t.artist} for t in rows]}

    @app.put("/api/playlists/{playlist_id}", dependencies=[auth])
    async def rename_playlist(playlist_id: int, payload: PlaylistCreate):
        import sqlite3
        try:
            db.rename_playlist(playlist_id, payload.name)
        except (ValueError, sqlite3.IntegrityError) as exc:
            raise HTTPException(409, detail="Нельзя изменить название плейлиста") from exc
        return {"changed": True}

    @app.delete("/api/playlists/{playlist_id}", dependencies=[auth])
    async def delete_playlist(playlist_id: int):
        try:
            db.delete_playlist(playlist_id)
        except ValueError as exc:
            raise HTTPException(409, detail="Нельзя удалить этот плейлист") from exc
        return {"deleted": True}

    @app.delete("/api/playlists/{playlist_id}/tracks/{track_id}", dependencies=[auth])
    async def remove_playlist_track(playlist_id: int, track_id: int):
        if not db.get_playlist(playlist_id):
            raise HTTPException(404, detail="Плейлист не найден")
        return {"changed": db.remove_from(playlist_id, track_id)}

    @app.put("/api/playlists/{playlist_id}/tracks/{track_id}/position", dependencies=[auth])
    async def shift_playlist_track(playlist_id: int, track_id: int, payload: PlaylistMove):
        try:
            return {"changed": db.shift_playlist_track(playlist_id, track_id, payload.delta)}
        except ValueError as exc:
            raise HTTPException(409, detail="Трек больше не в плейлисте") from exc

    @app.post("/api/playlists/{playlist_id}/play", dependencies=[auth])
    async def play_playlist(playlist_id: int, payload: PlaylistPlay):
        import random
        info = db.get_playlist(playlist_id)
        if not info:
            raise HTTPException(404, detail="Плейлист не найден")
        ids = [t.id for t in db.playlist_tracks(playlist_id, 1001)]
        if len(ids) > 1000:
            raise HTTPException(409, detail="Больше 1000 треков в плейлисте")
        if payload.shuffle and payload.start_track_id is not None:
            raise HTTPException(422, detail="Выбери перемешивание или конкретный трек")
        if payload.start_track_id is not None:
            if payload.start_track_id not in ids:
                raise HTTPException(409, detail="Трек больше не в плейлисте")
            ids = ids[ids.index(payload.start_track_id):]
        if not ids:
            raise HTTPException(409, detail="Плейлист пуст")
        if payload.shuffle:
            random.shuffle(ids)
        queue_id = db.create_queue(ids, info["name"], "playlist")
        async with Bot(token=settings.token) as bot:
            await deliver_queue(bot, db, settings.owner_id, queue_id, send_lock)
        sent = db.queue_snapshot(queue_id)["cursor"]
        if not sent:
            raise HTTPException(502, detail="Telegram не смог отправить музыку")
        return {"queue_id": queue_id, "sent": sent}

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

    @app.get("/api/stats/wrapped", dependencies=[auth])
    async def wrapped_report(
        period: Literal["7d", "30d", "365d", "all"] = "30d",
    ):
        return wrapped_summary(db, period)

    @app.get("/api/wave/preset", dependencies=[auth])
    async def wave_preset():
        return {"preset": db.get_wave_preset()}

    @app.put("/api/wave/preset", dependencies=[auth])
    async def update_wave_preset(payload: WavePresetRequest):
        return {"preset": db.set_wave_preset(payload.preset)}

    @app.get("/api/queue/current", dependencies=[auth])
    async def queue_current():
        current = db.latest_queue_id()
        if current is None:
            return None
        result = db.queue_snapshot(current)
        result["filters"] = db.get_queue_filters(current)
        return result

    @app.post("/api/queue/{queue_id}/send", dependencies=[auth])
    async def queue_send_next(queue_id: int):
        try:
            before = db.queue_snapshot(queue_id)
        except ValueError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        async with Bot(token=settings.token) as bot:
            await deliver_queue(
                bot, db, settings.owner_id, queue_id, send_lock,
                expected_cursor=before["cursor"],
            )
        after = db.queue_snapshot(queue_id)
        advanced = after["cursor"] - before["cursor"]
        if advanced <= 0:
            raise HTTPException(
                409, detail="Очередь закончилась, занята или Telegram не ответил"
            )
        return {"queue_id": queue_id, "sent": advanced, "cursor": after["cursor"]}

    @app.get("/api/queue/{queue_id}", dependencies=[auth])
    async def queue_preview(queue_id: int):
        try:
            preview = db.queue_snapshot(queue_id)
            preview["filters"] = db.get_queue_filters(queue_id)
            return preview
        except ValueError as exc:
            raise HTTPException(404, detail=str(exc)) from exc

    @app.post("/api/queue/{queue_id}/items", dependencies=[auth])
    async def queue_insert(queue_id: int, request: QueueItemRequest):
        try:
            return db.edit_queue(
                queue_id, request.action, track_id=request.track_id,
                expected_cursor=request.expected_cursor,
            )
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @app.put("/api/queue/{queue_id}/items", dependencies=[auth])
    async def queue_reorder(queue_id: int, request: QueueMoveRequest):
        try:
            return db.edit_queue(
                queue_id, "move", position=request.position,
                to_position=request.to_position,
                expected_cursor=request.expected_cursor,
            )
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @app.delete("/api/queue/{queue_id}/items/{position}", dependencies=[auth])
    async def queue_remove(queue_id: int, position: int):
        try:
            return db.edit_queue(queue_id, "remove", position=position)
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

    @app.get("/api/queue/{queue_id}/filters", dependencies=[auth])
    async def queue_filters(queue_id: int):
        try:
            return db.get_queue_filters(queue_id)
        except ValueError as exc:
            raise HTTPException(404, detail=str(exc)) from exc

    @app.put("/api/queue/{queue_id}/filters", dependencies=[auth])
    async def queue_set_filters(queue_id: int, request: QueueFiltersRequest):
        try:
            return db.set_queue_filters(
                queue_id, min_rating=request.min_rating,
                liked_only=request.liked_only,
                no_repeat_today=request.no_repeat_today,
            )
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc)) from exc

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


