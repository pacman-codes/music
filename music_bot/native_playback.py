"""Native playback reports: separate from Telegram sends, idempotent per device."""
from __future__ import annotations

import re
from music_bot.recommend import artist_key


class NativePlayback:
    def __init__(self, library):
        self.db = library
        with library.con() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS native_plays(
              id INTEGER PRIMARY KEY,
              device_id TEXT NOT NULL, play_id TEXT NOT NULL,
              track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
              listened_ms INTEGER NOT NULL, duration_ms INTEGER,
              outcome TEXT CHECK(outcome IN ('skip','complete','stop')),
              started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(device_id,play_id));
            CREATE INDEX IF NOT EXISTS native_plays_recent ON native_plays(id DESC);
            """)

    def report(self, device, play_id, track_id, event, listened_ms, duration_ms):
        if not re.fullmatch(r"[a-f0-9]{32}",play_id):
            raise ValueError("Invalid play ID")
        if event not in {"start","skip","complete","stop"} or not 0<=listened_ms<=86400000:
            raise ValueError("Invalid report")
        if duration_ms is not None and not 0<duration_ms<=86400000:
            raise ValueError("Invalid duration")
        with self.db.con() as con:
            con.execute("BEGIN IMMEDIATE")
            if con.execute("SELECT 1 FROM tracks WHERE id=?",(track_id,)).fetchone() is None:
                raise ValueError("Track not found")
            row=con.execute("SELECT * FROM native_plays WHERE device_id=? AND play_id=?",
                            (device,play_id)).fetchone()
            if row and row["track_id"]!=track_id:
                raise ValueError("Play ID already belongs to another track")
            if row and row["outcome"] is not None:
                return False
            if event=="start" and row:
                return False
            listened=max(listened_ms,row["listened_ms"] if row else 0)
            if listened<2000:
                return False
            outcome=None if event=="start" else event
            if not row:
                con.execute("""INSERT INTO native_plays(device_id,play_id,track_id,listened_ms,duration_ms,outcome)
                               VALUES(?,?,?,?,?,?)""",(device,play_id,track_id,listened,duration_ms,outcome))
            else:
                con.execute("UPDATE native_plays SET listened_ms=?,duration_ms=?,outcome=? WHERE id=?",
                            (listened,duration_ms,outcome,row["id"]))
            # Completion requires substantial actual played time, not merely seeking
            # to the end. Late skips and stops are not negative preference signals.
            feedback=None
            if event=="complete" and duration_ms and listened>=duration_ms*0.8:
                feedback="complete"
            elif event=="skip" and duration_ms and listened<min(30000,duration_ms*0.25):
                feedback="skip"
            if feedback:
                con.execute("""INSERT OR IGNORE INTO playback_events(track_id,event,source,dedupe_key)
                               VALUES(?,?,'instrumented',?)""",(track_id,feedback,"native:"+device+":"+play_id))
            return True

    def context(self, repeat_window):
        with self.db.con() as con:
            rows=con.execute("""SELECT p.track_id,t.artist FROM native_plays p JOIN tracks t ON t.id=p.track_id
                               ORDER BY p.id DESC LIMIT 240""").fetchall()
        exposure={}
        for i,row in enumerate(rows):
            tid=row["track_id"]
            exposure[tid]=exposure.get(tid,0)+1/(1+i/25)
        return {"rotation_ids":{r["track_id"] for r in rows[:repeat_window]},
                "immediate_ids":{r["track_id"] for r in rows[:3]},
                "previous_artists":[artist_key(r["artist"]) for r in reversed(rows[:3])],
                "recent_ids":{r["track_id"] for r in rows[:60]},"exposure":exposure}

    def history(self, before=None):
        with self.db.con() as con:
            rows=con.execute("""SELECT p.id,p.track_id,t.title,t.artist,p.listened_ms,p.duration_ms,
                                p.outcome,p.started_at FROM native_plays p JOIN tracks t ON t.id=p.track_id
                                WHERE (? IS NULL OR p.id<?) ORDER BY p.id DESC LIMIT 51""",(before,before)).fetchall()
        return {"results":[dict(r) for r in rows[:50]],"next_before":rows[49]["id"] if len(rows)>50 else None}
