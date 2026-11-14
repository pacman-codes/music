from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Track:
    id: int
    file_id: str
    file_unique_id: str
    title: str
    artist: str

class Library:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def con(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def init(self):
        with self.con() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS tracks(
              id INTEGER PRIMARY KEY, file_id TEXT NOT NULL,
              file_unique_id TEXT NOT NULL UNIQUE, title TEXT NOT NULL, artist TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS playlists(
              id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
              kind TEXT NOT NULL DEFAULT 'custom' CHECK (kind IN ('liked','custom')));
            CREATE UNIQUE INDEX IF NOT EXISTS one_liked ON playlists(kind) WHERE kind='liked';
            CREATE TABLE IF NOT EXISTS playlist_tracks(
              playlist_id INTEGER REFERENCES playlists(id) ON DELETE CASCADE,
              track_id INTEGER REFERENCES tracks(id) ON DELETE CASCADE,
              position INTEGER NOT NULL,
              PRIMARY KEY(playlist_id,track_id));
            CREATE TABLE IF NOT EXISTS dislikes(
              track_id INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE);
            """)
            db.execute("INSERT OR IGNORE INTO playlists(name,kind) VALUES('Мне нравится','liked')")

    @staticmethod
    def track(row):
        return Track(row["id"], row["file_id"], row["file_unique_id"],
                     row["title"], row["artist"])

    def add_track(self, file_id: str, unique_id: str, title: str, artist: str) -> Track:
        if not file_id or not unique_id:
            raise ValueError("Missing Telegram file identifiers")
        with self.con() as db:
            db.execute("""INSERT INTO tracks(file_id,file_unique_id,title,artist)
                          VALUES(?,?,?,?)
                          ON CONFLICT(file_unique_id) DO UPDATE SET file_id=excluded.file_id""",
                       (file_id, unique_id, title[:250], artist[:250]))
            row = db.execute("SELECT * FROM tracks WHERE file_unique_id=?", (unique_id,)).fetchone()
            return self.track(row)

    def all_tracks(self, limit=1000):
        with self.con() as db:
            return [self.track(r) for r in db.execute(
                "SELECT * FROM tracks ORDER BY id DESC LIMIT ?", (limit,))]

    def liked_id(self):
        with self.con() as db:
            return db.execute("SELECT id FROM playlists WHERE kind='liked'").fetchone()[0]

    def playlists(self):
        with self.con() as db:
            return [dict(r) for r in db.execute(
                """SELECT p.id,p.name,p.kind,COUNT(pt.track_id) AS count
                   FROM playlists p LEFT JOIN playlist_tracks pt ON pt.playlist_id=p.id
                   GROUP BY p.id ORDER BY CASE WHEN p.kind='liked' THEN 0 ELSE 1 END,p.id""")]

    def create_playlist(self, name):
        name = name.strip()
        if not (1 <= len(name) <= 80) or name.casefold() == "мне нравится":
            raise ValueError("Invalid playlist name")
        with self.con() as db:
            return db.execute("INSERT INTO playlists(name,kind) VALUES(?,'custom')",
                              (name,)).lastrowid

    def add_to(self, playlist_id, track_id):
        with self.con() as db:
            if not db.execute("SELECT 1 FROM playlists WHERE id=?", (playlist_id,)).fetchone():
                raise ValueError("Playlist not found")
            if not db.execute("SELECT 1 FROM tracks WHERE id=?", (track_id,)).fetchone():
                raise ValueError("Track not found")
            pos = db.execute(
                "SELECT COALESCE(MAX(position),0)+1 FROM playlist_tracks WHERE playlist_id=?",
                (playlist_id,)).fetchone()[0]
            return db.execute("""INSERT OR IGNORE INTO playlist_tracks(playlist_id,track_id,position)
                                 VALUES(?,?,?)""", (playlist_id,track_id,pos)).rowcount > 0

    def like(self, track_id):
        return self.add_to(self.liked_id(), track_id)

    def playlist_tracks(self, playlist_id, limit=100):
        with self.con() as db:
            return [self.track(r) for r in db.execute(
                """SELECT t.* FROM tracks t JOIN playlist_tracks p ON t.id=p.track_id
                   WHERE p.playlist_id=? ORDER BY p.position LIMIT ?""",
                (playlist_id,limit))]

    def liked_ids(self):
        return {t.id for t in self.playlist_tracks(self.liked_id())}

    def dislike(self, track_id):
        with self.con() as db:
            if not db.execute("SELECT 1 FROM tracks WHERE id=?", (track_id,)).fetchone():
                raise ValueError("Track not found")
            db.execute("INSERT OR IGNORE INTO dislikes(track_id) VALUES(?)", (track_id,))

    def disliked_ids(self):
        with self.con() as db:
            return {r[0] for r in db.execute("SELECT track_id FROM dislikes")}
