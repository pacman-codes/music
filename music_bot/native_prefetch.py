"""Durable, bounded preparation of owner-approved audio on the native server."""
from __future__ import annotations

import asyncio
import os
import shutil
import time
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from music_bot.importer import MediaImportError, temporary_audio, validate_direct_audio_url
from music_bot.native_storage import NativeStorage
from music_bot.storage import Library

MAX_PENDING = 30
MAX_ATTEMPTS = 3
LEASE_SECONDS = 180
MIN_FREE = 1024**3


def configured_hosts():
    return tuple(x.strip().lower() for x in os.environ.get(
        "LAIN_PREFETCH_HOSTS","").split(",") if x.strip())


class Prefetch:
    def __init__(self, store, hosts=()):
        self.store, self.db, self.hosts = store, store.library, tuple(hosts)
        with self.db.con() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS native_sources(
              track_id INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
              url TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS native_prefetch_jobs(
              track_id INTEGER PRIMARY KEY REFERENCES native_sources(track_id) ON DELETE CASCADE,
              state TEXT NOT NULL CHECK(state IN ('queued','running','ready','failed')),
              attempts INTEGER NOT NULL DEFAULT 0,
              due INTEGER NOT NULL, lease TEXT, expires INTEGER,
              error TEXT);
            CREATE INDEX IF NOT EXISTS native_prefetch_due
              ON native_prefetch_jobs(state,due);
            """)

    def approve(self, track_id, url):
        """Trusted CLI only. No signed URLs, credentials or arbitrary client URLs."""
        validate_direct_audio_url(url,self.hosts)
        if urlsplit(url).query:
            raise ValueError("Use a stable source URL without query parameters")
        with self.db.con() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM tracks WHERE id=?",(track_id,)).fetchone() is None:
                raise ValueError("Track not found")
            running=db.execute("SELECT state FROM native_prefetch_jobs WHERE track_id=?",(track_id,)).fetchone()
            if running and running[0]=="running":
                raise ValueError("Source is currently being downloaded")
            db.execute("INSERT INTO native_sources VALUES(?,?) ON CONFLICT(track_id) DO UPDATE SET url=excluded.url",(track_id,url))
            db.execute("DELETE FROM native_prefetch_jobs WHERE track_id=?",(track_id,))

    def approved_ids(self):
        if not self.hosts:
            return set()
        with self.db.con() as db:
            rows=db.execute("SELECT track_id,url FROM native_sources").fetchall()
        result=set()
        for row in rows:
            try:
                validate_direct_audio_url(row["url"],self.hosts)
            except MediaImportError:
                continue
            result.add(row["track_id"])
        return result

    def enqueue(self, ids, *, now=None):
        now=int(time.time()) if now is None else now
        ids=list(dict.fromkeys(ids))
        if len(ids)>5:
            raise ValueError("Maximum 5 tracks per request")
        approved=self.approved_ids()
        ready=self.store.available_ids()
        results=[]
        with self.db.con() as db:
            db.execute("BEGIN IMMEDIATE")
            pending=db.execute("SELECT COUNT(*) FROM native_prefetch_jobs WHERE state IN ('queued','running')").fetchone()[0]
            for tid in ids:
                if tid in ready:
                    state="ready"
                elif tid not in approved:
                    state="unavailable"
                else:
                    row=db.execute("SELECT state,attempts FROM native_prefetch_jobs WHERE track_id=?",(tid,)).fetchone()
                    if row and row["state"]!="ready":
                        state=row["state"]
                    elif pending>=MAX_PENDING:
                        state="busy"
                    else:
                        db.execute("""INSERT INTO native_prefetch_jobs(track_id,state,due)
                            VALUES(?,'queued',?) ON CONFLICT(track_id) DO UPDATE SET
                            state='queued',attempts=0,due=excluded.due,lease=NULL,expires=NULL,error=NULL""",(tid,now))
                        pending+=1
                        state="queued"
                results.append({"track_id":tid,"state":state})
        return results

    def status(self, ids):
        ready=self.store.available_ids()
        approved=self.approved_ids()
        result=[]
        with self.db.con() as db:
            for tid in dict.fromkeys(ids):
                row=db.execute("SELECT state,attempts,error FROM native_prefetch_jobs WHERE track_id=?",(tid,)).fetchone()
                if tid in ready:
                    state="ready"
                elif tid not in approved:
                    state="unavailable"
                elif row and row["state"]!="ready":
                    state=row["state"]
                else:
                    state="not_queued"
                result.append({"track_id":tid,"state":state,"attempts":row["attempts"] if row else 0,
                               "error":row["error"] if row and state=="failed" else None})
        return result

    def claim(self, *, now=None):
        now=int(time.time()) if now is None else now
        if not self.hosts:
            return None
        lease=secrets.token_hex(16)
        with self.db.con() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""UPDATE native_prefetch_jobs SET state=CASE WHEN attempts>=? THEN 'failed' ELSE 'queued' END,
                       lease=NULL,expires=NULL,due=?,error='interrupted' WHERE state='running' AND expires<=?""",
                       (MAX_ATTEMPTS,now,now))
            # Globally only one active download, even with multiple worker processes.
            if db.execute("SELECT 1 FROM native_prefetch_jobs WHERE state='running'").fetchone():
                return None
            row=db.execute("""SELECT j.track_id,j.attempts,s.url FROM native_prefetch_jobs j
                       JOIN native_sources s USING(track_id) WHERE j.state='queued' AND j.due<=?
                       ORDER BY j.due,j.track_id LIMIT 1""",(now,)).fetchone()
            if not row:
                return None
            db.execute("UPDATE native_prefetch_jobs SET state='running',attempts=attempts+1,lease=?,expires=? WHERE track_id=?",
                       (lease,now+LEASE_SECONDS,row["track_id"]))
            return {"track_id":row["track_id"],"url":row["url"],"attempts":row["attempts"]+1,"lease":lease}

    def finish(self, job, success, error=None, *, now=None):
        now=int(time.time()) if now is None else now
        state="ready" if success else "failed" if job["attempts"]>=MAX_ATTEMPTS else "queued"
        delay=0 if success else 30*2**(job["attempts"]-1)
        with self.db.con() as db:
            return bool(db.execute("""UPDATE native_prefetch_jobs SET state=?,due=?,lease=NULL,expires=NULL,error=?
                WHERE track_id=? AND state='running' AND lease=?""",
                (state,now+delay,error,job["track_id"],job["lease"])).rowcount)

    async def run_one(self, downloader=temporary_audio):
        job=self.claim()
        if job is None:
            return False
        tid=job["track_id"]
        try:
            if self.store.audio(tid):
                self.finish(job,True)
                return True
            if shutil.disk_usage(self.store.root).free < MIN_FREE + 90*1024**2:
                self.finish(job,False,"low_disk")
                return True
            track=self.db.get_track(tid)
            if track is None:
                self.finish(job,False,"missing_track")
                return True
            validate_direct_audio_url(job["url"],self.hosts)
            async with downloader(job["url"],self.hosts) as path:
                self.store.import_file(path,title=track.title,artist=track.artist,track_id=tid)
            self.finish(job,True)
        except (MediaImportError, OSError, ValueError, TimeoutError):
            self.finish(job,False,"source_or_storage_error")
        return True


async def worker():
    root=Path(os.environ.get("LAIN_DATA_DIR","data/standalone"))
    prefetch=Prefetch(NativeStorage(Library(root/"music.db"),root/"audio"),configured_hosts())
    while True:
        worked=await prefetch.run_one()
        await asyncio.sleep(0.5 if worked else 3)


if __name__=="__main__":
    asyncio.run(worker())
