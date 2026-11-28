"""Standalone private music API. Does not require a bot token."""
from pathlib import Path
import os
from typing import Annotated, Literal

from fastapi import FastAPI, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from music_bot.native_storage import NativeStorage
from music_bot.native_prefetch import Prefetch, configured_hosts
from music_bot.selection import select_wave_batch
from music_bot.storage import Library


class PairRequest(BaseModel):
    code: str = Field(min_length=43,max_length=43)
    label: str = Field(min_length=1,max_length=80)


class PrefetchRequest(BaseModel):
    track_ids: list[Annotated[int, Field(gt=0,le=9223372036854775807,strict=True)]] = Field(min_length=1,max_length=5)


class Rating(BaseModel):
    score: int = Field(ge=1,le=10,strict=True)


class Reaction(BaseModel):
    status: Literal["maybe","add","never","later","interesting"]


def create_app(root: Path | None = None):
    root = Path(root or os.environ.get("LAIN_DATA_DIR","data/standalone"))
    db = Library(root/"music.db")
    store = NativeStorage(db,root/"audio")
    prefetch = Prefetch(store,configured_hosts())
    app = FastAPI(title="LAIN native",docs_url=None,redoc_url=None,openapi_url=None)

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
    def audio(track_id: int):
        if not 0<track_id<=9223372036854775807:
            raise HTTPException(404,"Audio unavailable")
        asset=store.audio(track_id)
        if not asset:
            raise HTTPException(404,"Audio unavailable")
        path,mime,_=asset
        return FileResponse(path,media_type=mime)

    @app.get("/v1/wave",dependencies=[auth])
    def wave(mode: Literal["balanced","discover","favorites"]="balanced"):
        available=store.available_ids()
        return {"results":public(select_wave_batch(db,mode,available_ids=available),available)}

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
        ready=select_wave_batch(db,mode,available_ids=available)
        missing=prefetch.approved_ids()-available
        # Failed jobs require an explicit operator reapproval, not endless retries.
        with db.con() as con:
            failed={row[0] for row in con.execute(
                "SELECT track_id FROM native_prefetch_jobs WHERE state='failed'")}
        upcoming=select_wave_batch(db,mode,count=5,available_ids=missing-failed)
        jobs=prefetch.enqueue([t.id for t in upcoming])
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

    return app
