"""Owner-only per-device queue checkpoints. No remote playback commands."""
from __future__ import annotations

import json
import time
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

TrackID = Annotated[int, Field(strict=True, gt=0, le=9223372036854775807)]


class Checkpoint(BaseModel):
    track_ids: list[TrackID] = Field(min_length=1,max_length=1000)
    index: int = Field(strict=True,ge=0,le=999)
    position_ms: int = Field(strict=True,ge=0,le=86400000)
    wave: Literal["balanced","discover","favorites"] | None = None

    @model_validator(mode="after")
    def valid_index(self):
        if self.index >= len(self.track_ids):
            raise ValueError("Current position outside queue")
        return self


class DeviceSync:
    def __init__(self,db,store):
        self.db,self.store=db,store
        with db.con() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS native_checkpoints(
                device_id TEXT PRIMARY KEY REFERENCES native_sessions(id) ON DELETE CASCADE,
                track_ids TEXT NOT NULL, current_index INTEGER NOT NULL,
                position_ms INTEGER NOT NULL, wave TEXT,
                revision INTEGER NOT NULL, updated_at INTEGER NOT NULL
            );
            """)

    def publish(self,device,payload,*,now=None):
        now=int(time.time()) if now is None else now
        ids=list(dict.fromkeys(payload.track_ids))
        with self.db.con() as con:
            con.execute("BEGIN IMMEDIATE")
            if not con.execute("SELECT 1 FROM native_sessions WHERE id=? AND expires_at>?",(device,now)).fetchone():
                raise HTTPException(401,"Session expired")
            # Chunk queries for compatibility with SQLite's variable limit.
            for start in range(0,len(ids),500):
                batch=ids[start:start+500]
                placeholders=",".join("?" for _ in batch)
                if con.execute(f"SELECT COUNT(*) FROM tracks WHERE id IN ({placeholders})",batch).fetchone()[0]!=len(batch):
                    raise HTTPException(422,"Queue includes unknown tracks")
            con.execute("""INSERT INTO native_checkpoints VALUES(?,?,?,?,?,1,?)
                ON CONFLICT(device_id) DO UPDATE SET
                track_ids=excluded.track_ids,current_index=excluded.current_index,
                position_ms=excluded.position_ms,wave=excluded.wave,
                revision=native_checkpoints.revision+1,updated_at=excluded.updated_at""",
                (device,json.dumps(payload.track_ids,separators=(",",":")),payload.index,
                 payload.position_ms,payload.wave,now))
            revision=con.execute("SELECT revision FROM native_checkpoints WHERE device_id=?",(device,)).fetchone()[0]
        return {"revision":revision,"updated_at":now}

    def devices(self,device,*,now=None):
        now=int(time.time()) if now is None else now
        with self.db.con() as con:
            rows=con.execute("""SELECT s.id,s.label,s.expires_at,c.track_ids,c.current_index,
                c.position_ms,c.updated_at,c.revision FROM native_sessions s
                LEFT JOIN native_checkpoints c ON c.device_id=s.id
                WHERE s.expires_at>? ORDER BY c.updated_at DESC,s.created_at DESC LIMIT 100""",(now,)).fetchall()
            result=[]
            for row in rows:
                item={"id":row["id"],"label":row["label"],"current":row["id"]==device,
                      "expires_at":row["expires_at"],"checkpoint":None}
                if row["track_ids"]:
                    ids=json.loads(row["track_ids"])
                    tid=ids[row["current_index"]]
                    track=con.execute("SELECT title,artist FROM tracks WHERE id=?",(tid,)).fetchone()
                    item["checkpoint"]={"track_id":tid,"title":track["title"] if track else None,
                        "artist":track["artist"] if track else None,"position_ms":row["position_ms"],
                        "updated_at":row["updated_at"],"revision":row["revision"],"count":len(ids)}
                result.append(item)
        return result

    def get(self,device,public,*,now=None):
        now=int(time.time()) if now is None else now
        with self.db.con() as con:
            row=con.execute("""SELECT c.*,s.label FROM native_checkpoints c
                JOIN native_sessions s ON s.id=c.device_id
                WHERE c.device_id=? AND s.expires_at>?""",(device,now)).fetchone()
            if not row:raise HTTPException(404,"Checkpoint unavailable")
            ids=json.loads(row["track_ids"])
            current=ids[row["current_index"]]
            rows={}
            unique=list(dict.fromkeys(ids))
            for start in range(0,len(unique),500):
                batch=unique[start:start+500]
                placeholders=",".join("?" for _ in batch)
                for track in con.execute(f"SELECT * FROM tracks WHERE id IN ({placeholders})",batch):
                    rows[track["id"]]=self.db.track(track)
        available=self.store.available_ids()
        if current not in rows or current not in available:
            raise HTTPException(409,"Current audio unavailable")
        # Preserve repeats and original order; skip unavailable non-current audio.
        kept=[rows[tid] for tid in ids if tid in rows and tid in available]
        index=sum(tid in rows and tid in available for tid in ids[:row["current_index"]])
        return {"device_id":device,"label":row["label"],"revision":row["revision"],
                "updated_at":row["updated_at"],"results":public(kept,available),
                "index":index,"position_ms":row["position_ms"],"wave":row["wave"],
                "omitted":len(ids)-len(kept)}

    def clear(self,device):
        with self.db.con() as con:
            return bool(con.execute("DELETE FROM native_checkpoints WHERE device_id=?",(device,)).rowcount)


def install_sync(app,db,store,auth,public):
    sync=DeviceSync(db,store)

    @app.get("/v1/devices")
    def devices(device: str=auth):
        return {"results":sync.devices(device)}

    @app.put("/v1/checkpoint")
    def publish(payload: Checkpoint,device: str=auth):
        return sync.publish(device,payload)

    @app.delete("/v1/checkpoint")
    def clear(device: str=auth):
        return {"changed":sync.clear(device)}

    @app.get("/v1/devices/{device_id}/checkpoint",dependencies=[auth])
    def checkpoint(device_id: str):
        if len(device_id)!=32 or any(ch not in "0123456789abcdef" for ch in device_id):
            raise HTTPException(404,"Checkpoint unavailable")
        return sync.get(device_id,public)
