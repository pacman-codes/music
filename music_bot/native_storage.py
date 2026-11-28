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

    def import_file(self, source: Path, *, title: str, artist: str, track_id=None):
        """Operator-only import; atomic publication, no remote URLs or Telegram calls."""
        import os
        import tempfile
        mime = {".mp3":"audio/mpeg",".m4a":"audio/mp4",".flac":"audio/flac",
                ".ogg":"audio/ogg"}.get(source.suffix.lower())
        if not mime or not source.is_file():
            raise ValueError("Expected local MP3, M4A, FLAC or OGG")
        title, artist = title.strip(), artist.strip()
        if not title or not artist or len(title)>250 or len(artist)>250:
            raise ValueError("Title and artist must be 1–250 characters")
        if track_id is not None and self.library.get_track(track_id) is None:
            raise ValueError("Track not found")
        sha = hashlib.sha256()
        size = 0
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root,delete=False) as out:
                temp = Path(out.name)
                with source.open("rb") as stream:
                    while block := stream.read(1024*1024):
                        size += len(block)
                        if size > 200*1024*1024:
                            raise ValueError("File exceeds 200 MiB")
                        sha.update(block)
                        out.write(block)
                if not size:
                    raise ValueError("Empty file")
                out.flush()
                os.fsync(out.fileno())
            ident = sha.hexdigest()
            target = self.root / ident
            with self.library.con() as db:
                db.execute("BEGIN IMMEDIATE")
                if track_id is None:
                    existing = db.execute("SELECT track_id FROM native_audio WHERE sha256=? ORDER BY track_id LIMIT 1",(ident,)).fetchone()
                    if existing:
                        track_id = existing[0]
                    else:
                        uid = "native:"+ident
                        db.execute("""INSERT OR IGNORE INTO tracks(file_id,file_unique_id,title,artist,added_at)
                                      VALUES(?,?,?,?,CURRENT_TIMESTAMP)""",(uid,uid,title,artist))
                        track_id = db.execute("SELECT id FROM tracks WHERE file_unique_id=?",(uid,)).fetchone()[0]
                previous = db.execute("SELECT sha256 FROM native_audio WHERE track_id=?",(track_id,)).fetchone()
                if previous and previous[0]!=ident:
                    raise ValueError("Track already has different local audio")
                if target.is_symlink():
                    raise ValueError("Unsafe media path")
                os.replace(temp,target)
                temp = None
                db.execute("INSERT OR IGNORE INTO native_audio(track_id,sha256,size,mime) VALUES(?,?,?,?)",
                           (track_id,ident,size,mime))
            return track_id
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
