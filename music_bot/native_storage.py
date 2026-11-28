"""Private native client storage. No Telegram credentials or network calls."""
from __future__ import annotations

import hashlib
import re
import secrets
import time
from pathlib import Path

from music_bot.storage import Library

TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}\Z")
PAIR_TTL = 600
SESSION_TTL = 90 * 86400


def digest(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class NativeStorage:
    def __init__(self, library: Library, media_root: Path):
        self.library = library
        self.root = media_root.resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with library.con() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS native_pairings(
              digest TEXT PRIMARY KEY, expires_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS native_sessions(
              id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, label TEXT NOT NULL,
              created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS native_audio(
              track_id INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
              sha256 TEXT NOT NULL, size INTEGER NOT NULL CHECK(size>0),
              mime TEXT NOT NULL CHECK(mime IN ('audio/mpeg','audio/mp4','audio/flac','audio/ogg')),
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            """)

    def issue_pairing(self, *, now=None):
        now = int(time.time()) if now is None else now
        token = secrets.token_urlsafe(32)
        with self.library.con() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM native_pairings WHERE expires_at<=?", (now,))
            db.execute("INSERT INTO native_pairings VALUES(?,?)", (digest(token),now+PAIR_TTL))
        return token

    def exchange(self, code, label, *, now=None):
        if not isinstance(code,str) or not TOKEN_RE.fullmatch(code):
            raise ValueError("Invalid pairing")
        label = label.strip()
        if not 1<=len(label)<=80:
            raise ValueError("Invalid device label")
        now = int(time.time()) if now is None else now
        token, ident = secrets.token_urlsafe(32), secrets.token_hex(16)
        with self.library.con() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("DELETE FROM native_pairings WHERE digest=? AND expires_at>?",
                             (digest(code),now))
            if row.rowcount != 1:
                raise ValueError("Invalid pairing")
            db.execute("DELETE FROM native_sessions WHERE expires_at<=?", (now,))
            db.execute("INSERT INTO native_sessions VALUES(?,?,?,?,?)",
                       (ident,digest(token),label,now,now+SESSION_TTL))
        return {"token":token,"device_id":ident,"expires_at":now+SESSION_TTL}

    def authenticate(self, token, *, now=None):
        if not isinstance(token,str) or not TOKEN_RE.fullmatch(token):
            return None
        now = int(time.time()) if now is None else now
        with self.library.con() as db:
            row=db.execute("SELECT id FROM native_sessions WHERE digest=? AND expires_at>?",
                           (digest(token),now)).fetchone()
        return row[0] if row else None

    def devices(self):
        with self.library.con() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,label,created_at,expires_at FROM native_sessions ORDER BY created_at DESC")]

    def revoke(self, ident):
        with self.library.con() as db:
            return bool(db.execute("DELETE FROM native_sessions WHERE id=?", (ident,)).rowcount)

    def audio(self, track_id):
        with self.library.con() as db:
            row=db.execute("SELECT sha256,size,mime FROM native_audio WHERE track_id=?",
                           (track_id,)).fetchone()
        if not row:
            return None
        sha=row["sha256"]
        if not re.fullmatch(r"[a-f0-9]{64}",sha):
            return None
        path=self.root / sha
        if path.is_symlink() or not path.is_file() or path.stat().st_size != row["size"]:
            return None
        return path,row["mime"],row["size"]

    def available_ids(self):
        with self.library.con() as db:
            rows=db.execute("SELECT track_id,sha256,size FROM native_audio").fetchall()
        available=set()
        for row in rows:
            sha=row["sha256"]
            if not re.fullmatch(r"[a-f0-9]{64}",sha):
                continue
            path=self.root/sha
            try:
                if not path.is_symlink() and path.is_file() and path.stat().st_size==row["size"]:
                    available.add(row["track_id"])
            except OSError:
                continue
        return available
