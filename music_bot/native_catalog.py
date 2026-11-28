"""Native catalog and collection routes, shared metadata and explicit source matching."""
from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import asdict
from typing import Literal

from fastapi import HTTPException, Query
from pydantic import BaseModel, Field

from music_bot.albums import find_albums, album_tracks, valid_album_id, album_cover_urls, cached_album_group
from music_bot.artists import search_artists, artist_discography, group_album
from music_bot.catalog import CatalogError, valid_mbid
from music_bot.importer import MediaImportError
from music_bot.personalized import personalized_discovery
from music_bot.personal_albums import recommend_albums
from music_bot.smart_playlists import RULES, BASE
from music_bot.taste_profile import music_profile


class Decision(BaseModel):
    status: Literal["maybe","add","never","later","interesting"]


class Save(BaseModel):
    saved: bool = Field(strict=True)


class PlaylistName(BaseModel):
    name: str = Field(min_length=1,max_length=80)


class TrackID(BaseModel):
    track_id: int = Field(gt=0,le=9223372036854775807,strict=True)


class AlbumMatch(TrackID):
    recording_id: str = Field(min_length=4,max_length=36)


class Move(BaseModel):
    delta: Literal[-1,1]


def install_native_catalog(app, db, store, prefetch, catalog, auth, public):
    deps=[auth]

    def known_recording(ident):
        if not valid_mbid(ident):
            raise HTTPException(422,"Invalid recording ID")
        ident=ident.lower()
        row=db.get_candidate(ident)
        if row is None:
            raise HTTPException(404,"Recording not found")
        return ident

    def known_artist(ident):
        if not valid_mbid(ident):
            raise HTTPException(422,"Invalid artist ID")
        row=db.get_artist(ident.lower())
        if row is None:
            raise HTTPException(404,"Artist not found")
        return row

    def recording_rows(rows):
        linked=db.resolved_catalog_tracks()
        approved=prefetch.approved_ids()
        indexed=prefetch.indexed_recordings()
        ready=store.available_ids()
        decisions=db.catalog_verdicts()
        wanted=db.wishlist_ids()
        result=[]
        for row in rows:
            item=row.public() if hasattr(row,"public") else dict(row)
            ident=item["mbid"]
            local=linked.get(ident)
            item.update(local_track_id=local,playable=local in ready,
                        preparable=local in approved or ident in indexed,decision=decisions.get(ident),
                        in_wishlist=ident in wanted)
            result.append(item)
        return result

    async def limited(key, work):
        lease=db.acquire_media_import("taste",key,ttl=180)
        if lease is None:
            raise HTTPException(409,"Catalog request already running")
        try:
            return await asyncio.wait_for(work(),timeout=90)
        except (CatalogError,TimeoutError) as exc:
            raise HTTPException(503,"Music catalog unavailable") from exc
        except ValueError as exc:
            raise HTTPException(422,"Invalid catalog request") from exc
        finally:
            db.release_media_import("taste",key,lease)

    @app.get("/v1/discovery/search",dependencies=deps)
    async def search(q: str=Query(min_length=2,max_length=100), variants: bool=False):
        rows=await limited("native-catalog-search",lambda:catalog.search(q,limit=20,allow_variants=variants))
        db.remember_candidates(rows)
        return {"results":recording_rows(rows)}

    @app.get("/v1/discovery/for-you",dependencies=deps)
    async def discover(difficulty: Literal["easy","medium","hard"]="medium"):
        seeds,rows=await limited("native-discovery",lambda:personalized_discovery(db,catalog,difficulty=difficulty))
        db.remember_candidates(rows)
        return {"seed_artists":seeds,"results":recording_rows(rows)}

    @app.get("/v1/discovery/wishlist",dependencies=deps)
    def wishlist():
        return {"results":recording_rows(db.wishlist(100))}

    @app.get("/v1/discovery/{ident}",dependencies=deps)
    def recording_detail(ident: str):
        return recording_rows([db.get_candidate(known_recording(ident))])[0]

    @app.post("/v1/discovery/{ident}/prepare",dependencies=deps)
    def prepare_recording(ident: str):
        ident=known_recording(ident)
        try:
            job=prefetch.prepare_recording(ident)
        except (ValueError,MediaImportError) as exc:
            raise HTTPException(409,"Approved source unavailable") from exc
        return {**job,"recording":recording_rows([db.get_candidate(ident)])[0]}

    @app.put("/v1/discovery/{ident}/reaction",dependencies=deps)
    def decision(ident: str,payload: Decision):
        db.set_catalog_verdict(known_recording(ident),payload.status)
        return {"ok":True}

    @app.put("/v1/discovery/{ident}/saved",dependencies=deps)
    def save_recording(ident: str,payload: Save):
        ident=known_recording(ident)
        changed=db.wishlist_add(ident) if payload.saved else db.wishlist_remove(ident)
        return {"changed":changed,"saved":payload.saved}

    @app.put("/v1/discovery/{ident}/match",dependencies=deps)
    def match_recording(ident: str,payload: TrackID):
        ident=known_recording(ident)
        if not db.get_track(payload.track_id):
            raise HTTPException(404,"Track not found")
        current=db.resolved_catalog_tracks().get(ident)
        if current is not None and current!=payload.track_id:
            raise HTTPException(409,"Recording already linked")
        previous=db.recording_for_tracks().get(payload.track_id)
        if previous is not None and previous["mbid"]!=ident:
            raise HTTPException(409,"Track already linked to another recording")
        if current==payload.track_id:return {"ok":True}
        try:
            db.confirm_recording_match(payload.track_id,ident)
        except ValueError as exc:
            raise HTTPException(409,"Recording match conflicts with an existing link") from exc
        return {"ok":True}

    @app.get("/v1/albums",dependencies=deps)
    def saved_albums(offset: int=Query(0,ge=0,le=100000)):
        rows=db.saved_albums(offset=offset,limit=21)
        return {"results":[{**r,"cover_urls":album_cover_urls(r["mbid"],cached_album_group(catalog,r["mbid"]))} for r in rows[:20]],
                "has_more":len(rows)>20}

    @app.get("/v1/albums/search",dependencies=deps)
    async def search_album(q: str=Query(min_length=2,max_length=90)):
        rows=await limited("native-album-search",lambda:find_albums(catalog,q,exact=False))
        return {"results":[{**asdict(r),"saved":bool(db.get_saved_album(r.mbid)),
                           "cover_urls":album_cover_urls(r.mbid,r.group)} for r in rows]}

    @app.get("/v1/albums/for-you",dependencies=deps)
    async def album_recommendations(page: int=Query(0,ge=0,le=4)):
        return await limited("native-album-recommend",lambda:recommend_albums(db,catalog,page=page))

    async def load_album(ident):
        if not valid_album_id(ident):
            raise HTTPException(422,"Invalid album ID")
        ident=ident.lower()
        title,artist,rows=await limited("native-album-detail",lambda:album_tracks(catalog,ident))
        if not rows or len(rows)>100:
            raise HTTPException(409,"Album track list unavailable or too large")
        db.remember_candidates([r for r in rows if valid_mbid(r.mbid)])
        return ident,title,artist,rows

    @app.get("/v1/albums/{ident}",dependencies=deps)
    async def album(ident: str):
        ident,title,artist,rows=await load_album(ident)
        resolved=db.resolved_catalog_tracks()
        manual=db.album_audio_links()
        ready=store.available_ids()
        approved=prefetch.approved_ids()
        indexed=prefetch.indexed_recordings()
        tracks=[]
        for r in rows:
            tid=resolved.get(r.mbid) if valid_mbid(r.mbid) else None
            if tid is None:tid=manual.get(r.mbid)
            tracks.append({"recording_id":r.mbid,"title":r.title,"artist":r.artist,
                           "duration_ms":r.duration_ms,"local_track_id":tid,
                           "playable":tid in ready,"preparable":tid in approved or r.mbid in indexed})
        return {"mbid":ident,"title":title,"artist":artist,"saved":bool(db.get_saved_album(ident)),
                "cover_urls":album_cover_urls(ident,cached_album_group(catalog,ident)),
                "tracks":tracks,"available_count":sum(t["playable"] for t in tracks)}

    @app.put("/v1/albums/{ident}/saved",dependencies=deps)
    async def save_album(ident: str,payload: Save):
        if not valid_album_id(ident):
            raise HTTPException(422,"Invalid album ID")
        if payload.saved:
            ident,title,artist,rows=await load_album(ident)
            db.save_album(ident,title,artist,len(rows))
        else:
            db.remove_saved_album(ident.lower())
        return {"saved":payload.saved}

    @app.post("/v1/albums/{ident}/prepare",dependencies=deps)
    async def prepare_album(ident: str):
        _,_,_,rows=await load_album(ident)
        ready=store.available_ids()
        linked=db.resolved_catalog_tracks()
        manual=db.album_audio_links()
        approved=prefetch.approved_ids()
        indexed=prefetch.indexed_recordings()
        jobs=[]
        for row in rows:
            tid=linked.get(row.mbid,manual.get(row.mbid))
            if tid in ready:
                continue
            if len(jobs)>=5:break
            if tid in approved:
                job=prefetch.enqueue([tid])[0]
            elif row.mbid in indexed:
                try:job=prefetch.prepare_recording(row.mbid)
                except (ValueError,MediaImportError):continue
            else:continue
            if job["state"] not in {"failed","unavailable"}:
                jobs.append({"recording_id":row.mbid,**job})
        return {"results":jobs}

    @app.put("/v1/albums/{ident}/tracks/{position}/match",dependencies=deps)
    async def match_album_track(ident: str,position: int,payload: AlbumMatch):
        _,_,_,rows=await load_album(ident)
        if not 0<=position<len(rows) or rows[position].mbid!=payload.recording_id:
            raise HTTPException(409,"Album track list changed")
        resolved=db.resolved_catalog_tracks().get(payload.recording_id)
        if resolved is not None and resolved!=payload.track_id:
            raise HTTPException(409,"Recording already linked")
        if not db.get_track(payload.track_id):
            raise HTTPException(404,"Track not found")
        try:
            changed=db.confirm_album_audio(payload.recording_id,payload.track_id)
        except ValueError as exc:
            raise HTTPException(409,"Album track already associated") from exc
        return {"changed":changed}

    @app.get("/v1/artists",dependencies=deps)
    def saved_artists(offset: int=Query(0,ge=0,le=100000)):
        rows=db.saved_artists(offset=offset,limit=21)
        return {"results":rows[:20],"has_more":len(rows)>20}

    @app.get("/v1/artists/search",dependencies=deps)
    async def artist_search(q: str=Query(min_length=2,max_length=90)):
        rows=await limited("native-artist-search",lambda:search_artists(catalog,q))
        db.remember_artists(rows)
        return {"results":[db.get_artist(r.mbid) for r in rows]}

    @app.get("/v1/artists/{ident}",dependencies=deps)
    def artist_detail(ident: str,page: int=Query(0,ge=0,le=49)):
        row=known_artist(ident)
        tracks=db.artist_library_tracks(row["mbid"],row["name"])[:1000]
        return {**row,"results":public(tracks[page*20:page*20+20],store.available_ids()),
                "has_more":len(tracks)>(page+1)*20}

    @app.put("/v1/artists/{ident}/saved",dependencies=deps)
    def artist_save(ident: str,payload: Save):
        db.set_artist_saved(known_artist(ident)["mbid"],payload.saved)
        return {"saved":payload.saved}

    @app.get("/v1/artists/{ident}/discography",dependencies=deps)
    async def discography(ident: str,page: int=Query(0,ge=0,le=999)):
        row=known_artist(ident)
        rows,more=await limited("native-discography",lambda:artist_discography(catalog,row["mbid"],page))
        return {"results":[asdict(r) for r in rows],"has_more":more}

    @app.get("/v1/release-groups/{ident}",dependencies=deps)
    async def release_group(ident: str):
        if not valid_mbid(ident):
            raise HTTPException(422,"Invalid release group")
        row=await limited("native-release-group",lambda:group_album(catalog,ident.lower()))
        return {"release_id":row.mbid}

    def playlist_id(ident,custom=False):
        row=db.get_playlist(ident) if 0<ident<=9223372036854775807 else None
        if not row:
            raise HTTPException(404,"Playlist not found")
        if custom and row["kind"]!="custom":
            raise HTTPException(409,"Built-in playlist cannot be edited this way")
        return ident

    @app.post("/v1/playlists",dependencies=deps,status_code=201)
    def create_playlist(payload: PlaylistName):
        try:return {"id":db.create_playlist(payload.name)}
        except (ValueError,sqlite3.IntegrityError) as exc:
            raise HTTPException(409,"Invalid or duplicate playlist name") from exc

    @app.put("/v1/playlists/{ident}",dependencies=deps)
    def rename(ident: int,payload: PlaylistName):
        try:db.rename_playlist(playlist_id(ident,True),payload.name)
        except (ValueError,sqlite3.IntegrityError) as exc:
            raise HTTPException(409,"Invalid or duplicate playlist name") from exc
        return {"ok":True}

    @app.delete("/v1/playlists/{ident}",dependencies=deps)
    def delete(ident: int):
        db.delete_playlist(playlist_id(ident,True))
        return {"ok":True}

    @app.post("/v1/playlists/{ident}/tracks",dependencies=deps)
    def add(ident: int,payload: TrackID):
        try:return {"changed":db.add_to(playlist_id(ident),payload.track_id)}
        except ValueError as exc:raise HTTPException(404,"Track not found") from exc

    @app.delete("/v1/playlists/{ident}/tracks/{tid}",dependencies=deps)
    def remove(ident: int,tid: int):
        if not 0<tid<=9223372036854775807:raise HTTPException(404,"Track not found")
        return {"changed":db.remove_from(playlist_id(ident),tid)}

    @app.put("/v1/playlists/{ident}/tracks/{tid}/position",dependencies=deps)
    def move(ident: int,tid: int,payload: Move):
        if not 0<tid<=9223372036854775807:raise HTTPException(404,"Track not found")
        try:return {"changed":db.shift_playlist_track(playlist_id(ident,True),tid,payload.delta)}
        except ValueError as exc:raise HTTPException(404,"Track not found") from exc

    @app.get("/v1/playlists/{ident}/queue",dependencies=deps)
    def playlist_queue(ident: int):
        rows=db.playlist_tracks(playlist_id(ident),1001)
        if len(rows)>1000:raise HTTPException(409,"Playlist exceeds 1000 tracks")
        return {"results":public(rows,store.available_ids())}

    # Adapt history-based rules to native playback, never Telegram deliveries.
    native_rules={
        key:(rule.title,rule.description,rule.where,rule.order)
        for key,rule in RULES.items()
    }
    native_rules["unheard"]=("Ещё не слушал","Нет событий в плеере LAIN.",
        "NOT EXISTS (SELECT 1 FROM native_plays h WHERE h.track_id=t.id)","t.id DESC")
    native_rules["forgotten"]=("Забытое избранное","Нет воспроизведения в LAIN за 30 дней.",
        """v.status='add' AND NOT EXISTS (SELECT 1 FROM native_plays h
        WHERE h.track_id=t.id AND h.started_at>=datetime('now','-30 days'))""","t.id DESC")

    @app.get("/v1/smart-playlists",dependencies=deps)
    def smart_list():
        ready=store.available_ids()
        with db.con() as con:
            items=[]
            for key,(title,description,where,order) in native_rules.items():
                ids=[r[0] for r in con.execute("SELECT t.id"+BASE+where)]
                items.append({"key":key,"title":title,"description":description,
                              "count":len(ids),"available_count":sum(i in ready for i in ids)})
        return {"results":items}

    @app.get("/v1/smart-playlists/{key}",dependencies=deps)
    def smart_detail(key: str):
        rule=native_rules.get(key)
        if not rule:raise HTTPException(404,"Unknown smart playlist")
        title,description,where,order=rule
        with db.con() as con:
            rows=[db.track(r) for r in con.execute("SELECT t.*"+BASE+where+" ORDER BY "+order+" LIMIT 1001")]
        return {"title":title,"description":description,"results":public(rows[:1000],store.available_ids()),
                "truncated":len(rows)>1000}

    @app.get("/v1/taste/profile",dependencies=deps)
    def profile():
        data=music_profile(db)
        data["available_count"]=len(store.available_ids())
        return data
