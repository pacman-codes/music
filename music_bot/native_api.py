"""Standalone private music API. Does not require a bot token."""
from pathlib import Path
from contextlib import asynccontextmanager
from music_bot.catalog import MusicCatalog
from music_bot.native_catalog import install_native_catalog
import os
from typing import Annotated, Literal

from fastapi import FastAPI, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from music_bot.native_storage import NativeStorage
from music_bot.native_playback import NativePlayback
from music_bot.native_media import MediaProcessor
from music_bot.native_sync import install_sync
from music_bot.native_prefetch import Prefetch, configured_hosts
from music_bot.selection import select_wave_batch
from music_bot.storage import Library


class PairRequest(BaseModel):
    code: str = Field(min_length=43,max_length=43)
    label: str = Field(min_length=1,max_length=80)


class PrefetchRequest(BaseModel):
    track_ids: list[Annotated[int, Field(gt=0,le=9223372036854775807,strict=True)]] = Field(min_length=1,max_length=5)


class PlaybackReport(BaseModel):
    play_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    track_id: int = Field(gt=0,le=9223372036854775807,strict=True)
    event: Literal["start","progress","skip","complete","stop"]
    listened_ms: int = Field(ge=0,le=86400000,strict=True)
    duration_ms: int | None = Field(default=None,gt=0,le=86400000,strict=True)


class WaveSettings(BaseModel):
    diversity: int = Field(ge=0,le=100,strict=True)
    repeat_window: int = Field(ge=0,le=100,strict=True)
    preset: Literal["balanced","familiar","explore"]


class Rating(BaseModel):
    score: int = Field(ge=1,le=10,strict=True)


class Reaction(BaseModel):
    status: Literal["maybe","add","never","later","interesting"]


def create_app(root: Path | None = None, catalog=None):
    root = Path(root or os.environ.get("LAIN_DATA_DIR","data/standalone"))
    db = Library(root/"music.db")
    store = NativeStorage(db,root/"audio")
    prefetch = Prefetch(store,configured_hosts())
    playback = NativePlayback(db)
    media = MediaProcessor(store)
    catalog_client=catalog or MusicCatalog(db.path)
    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            if catalog is None:
                await catalog_client.close()
    app = FastAPI(title="LAIN native",docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)

    def owner(authorization: Annotated[str|None,Header()] = None):
        scheme, _, token = (authorization or "").partition(" ")
        device = store.authenticate(token) if scheme.lower()=="bearer" else None
        if not device:
            raise HTTPException(401,"Authorization required",headers={"WWW-Authenticate":"Bearer"})
        return device

    auth = Depends(owner)

    @app.middleware("http")
    async def headers(request,call_next):
        response = await call_next(request)
        response.headers["Cache-Control"]="private, no-store"
        response.headers["X-Content-Type-Options"]="nosniff"
        response.headers["Referrer-Policy"]="no-referrer"
        return response

    @app.get("/healthz")
    def health():
        return {"ok":True}

    @app.post("/v1/pair")
    def pair(payload: PairRequest):
        try:
            return store.exchange(payload.code,payload.label)
        except ValueError:
            raise HTTPException(401,"Invalid or expired pairing") from None

    @app.delete("/v1/session")
    def logout(device: str = auth):
        store.revoke(device)
        return {"ok":True}

    def public(rows, available):
        likes,ratings,verdicts=db.liked_ids(),db.ratings(),db.track_verdicts()
        return [{"id":t.id,"title":t.title,"artist":t.artist,
                 "playable":t.id in available,"liked":t.id in likes,
                 "rating":ratings.get(t.id),"decision":verdicts.get(t.id)}
                for t in rows]

    @app.get("/v1/tracks",dependencies=[auth])
    def tracks(q: str=Query("",max_length=100), before: int=Query(9223372036854775807,ge=1,le=9223372036854775807)):
        found=[]
        query=q.strip().casefold()
        with db.con() as con:
            for row in con.execute("SELECT * FROM tracks WHERE id<? ORDER BY id DESC",(before,)):
                if not query or query in row["title"].casefold() or query in row["artist"].casefold():
                    found.append(db.track(row))
                    if len(found)==51:
                        break
        return {"results":public(found[:50],store.available_ids()),
                "next_before":found[49].id if len(found)>50 else None}

    @app.get("/v1/tracks/{track_id}/audio",dependencies=[auth])
    def audio(track_id: int,quality: Literal['original','economy']='original'):
        if not 0<track_id<=9223372036854775807:
            raise HTTPException(404,"Audio unavailable")
        asset=store.audio(track_id)
        if not asset:
            raise HTTPException(404,"Audio unavailable")
        if quality=="economy":
            reduced=media.asset(track_id)
            if reduced is None:
                raise HTTPException(409,"Economy representation not prepared")
            path,mime,_,actual=reduced
        else:
            path,mime,_=asset
            actual="original"
        return FileResponse(path,media_type=mime,headers={"X-LAIN-Representation":actual})

    @app.post("/v1/media/prepare",dependencies=[auth])
    def prepare_media(payload: PrefetchRequest):
        media.request(payload.track_ids)
        return {"results":[{"track_id":tid,**media.info(tid)} for tid in payload.track_ids]}

    @app.get("/v1/tracks/{track_id}/analysis",dependencies=[auth])
    def analysis(track_id: int):
        if not 0<track_id<=9223372036854775807 or not db.get_track(track_id):
            raise HTTPException(404,"Track not found")
        return media.info(track_id)

    @app.get("/v1/tracks/{track_id}/similar",dependencies=[auth])
    def acoustic_similar(track_id: int):
        if not 0<track_id<=9223372036854775807 or not db.get_track(track_id):
            raise HTTPException(404,"Track not found")
        ranked=media.similar(track_id)
        rows=[t for tid,_ in ranked if (t:=db.get_track(tid)) is not None]
        distances=dict(ranked)
        return {"method":"acoustic_descriptors_v1","analysis_state":media.info(track_id)["analysis_state"],
                "results":[{**r,"descriptor_distance":distances[r["id"]]} for r in public(rows,store.available_ids())]}

    @app.post("/v1/playback")
    def report_playback(payload: PlaybackReport, device: str=auth):
        try:
            return {"changed":playback.report(device,**payload.model_dump())}
        except ValueError as exc:
            raise HTTPException(422,str(exc)) from None

    @app.get("/v1/listening-summary",dependencies=[auth])
    def listening_summary(period: Literal["7","30","365","all"]="30"):
        return playback.summary(None if period=="all" else int(period))

    @app.get("/v1/history",dependencies=[auth])
    def history(before: int|None=Query(None,ge=1,le=9223372036854775807)):
        return playback.history(before)

    @app.get("/v1/wave/settings",dependencies=[auth])
    def wave_settings():
        return {**db.get_wave_settings(),"preset":db.get_wave_preset()}

    @app.put("/v1/wave/settings",dependencies=[auth])
    def save_wave_settings(payload: WaveSettings):
        db.set_wave_settings(diversity=payload.diversity,repeat_window=payload.repeat_window)
        db.set_wave_preset(payload.preset)
        return {**db.get_wave_settings(),"preset":db.get_wave_preset()}

    def native_context():
        return {**playback.context(db.get_wave_settings()["repeat_window"]),"acoustic_affinity":media.affinity()}

    @app.get("/v1/wave",dependencies=[auth])
    def wave(mode: Literal["balanced","discover","favorites"]="balanced"):
        available=store.available_ids()
        return {"results":public(select_wave_batch(db,mode,available_ids=available,native_context=native_context()),available)}

    @app.post("/v1/prefetch",dependencies=[auth])
    def prepare(payload: PrefetchRequest):
        return {"results":prefetch.enqueue(payload.track_ids)}

    @app.post("/v1/prefetch/status",dependencies=[auth])
    def preparation_status(payload: PrefetchRequest):
        return {"results":prefetch.status(payload.track_ids)}

    @app.post("/v1/wave/prepare",dependencies=[auth])
    def prepare_wave(mode: Literal["balanced","discover","favorites"]="balanced"):
        """Return playable music now; schedule approved missing tracks separately."""
        available=store.available_ids()
        context=native_context()
        ready=select_wave_batch(db,mode,available_ids=available,native_context=context)
        missing=prefetch.approved_ids()-available
        # Failed jobs require an explicit operator reapproval, not endless retries.
        with db.con() as con:
            failed={row[0] for row in con.execute(
                "SELECT track_id FROM native_prefetch_jobs WHERE state='failed'")}
        upcoming=select_wave_batch(db,mode,count=5,available_ids=missing-failed,native_context=context)
        jobs=prefetch.enqueue([t.id for t in upcoming])
        extra=prefetch.prepare_saved_recordings(5-len(jobs),mode)
        for job in extra:
            track=db.get_track(job["track_id"]) if job["track_id"] else None
            if track is not None and all(t.id!=track.id for t in upcoming):
                upcoming.append(track)
                jobs.append(job)
        states={row["track_id"]:row["state"] for row in jobs}
        return {"results":public(ready,available),
                "preparing":[{**item,"state":states[item["id"]]}
                             for item in public(upcoming,available)]}

    @app.get("/v1/playlists",dependencies=[auth])
    def playlists():
        return db.playlists()

    @app.get("/v1/playlists/{playlist_id}",dependencies=[auth])
    def playlist(playlist_id: int,page: int=Query(0,ge=0,le=100000)):
        if not 0<playlist_id<=9223372036854775807 or not db.get_playlist(playlist_id):
            raise HTTPException(404,"Playlist not found")
        total,page,pages,rows=db.playlist_page(playlist_id,page,20)
        return {"total":total,"page":page,"pages":pages,"results":public(rows,store.available_ids())}

    @app.put("/v1/tracks/{track_id}/rating",dependencies=[auth])
    def rating(track_id: int,payload: Rating):
        if not 0<track_id<=9223372036854775807:
            raise HTTPException(404,"Track not found")
        try:
            db.set_rating(track_id,payload.score)
        except ValueError:
            raise HTTPException(404,"Track not found") from None
        return {"ok":True}

    @app.put("/v1/tracks/{track_id}/reaction",dependencies=[auth])
    def reaction(track_id: int,payload: Reaction):
        if not 0<track_id<=9223372036854775807:
            raise HTTPException(404,"Track not found")
        try:
            db.set_track_verdict(track_id,payload.status)
        except ValueError:
            raise HTTPException(404,"Track not found") from None
        return {"ok":True}

    install_native_catalog(app,db,store,prefetch,catalog_client,auth,public)
    install_sync(app,db,store,auth,public)
    return app
