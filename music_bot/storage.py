"""SQLite storage for an owner-only Telegram audio library.

No media binaries or remote URLs are stored here; only Telegram file IDs.
Existing v0.1 databases are upgraded additively with CREATE IF NOT EXISTS.
"""
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


@dataclass(frozen=True)
class QueueBatch:
    queue_id: int
    cursor: int
    label: str
    tracks: tuple[Track, ...]
    remaining_after: int


class Library:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def con(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return db

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
            CREATE TABLE IF NOT EXISTS queues(
              id INTEGER PRIMARY KEY,
              label TEXT NOT NULL,
              source_kind TEXT NOT NULL,
              cursor INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS queue_items(
              queue_id INTEGER NOT NULL REFERENCES queues(id) ON DELETE CASCADE,
              position INTEGER NOT NULL,
              track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
              PRIMARY KEY(queue_id, position));
            CREATE TABLE IF NOT EXISTS track_ratings(
              track_id INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
              score INTEGER NOT NULL CHECK(score BETWEEN 1 AND 10),
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS playback_events(
              id INTEGER PRIMARY KEY,
              track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
              event TEXT NOT NULL CHECK(event IN ('skip','complete')),
              source TEXT NOT NULL CHECK(source IN ('manual','instrumented')),
              dedupe_key TEXT UNIQUE,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE INDEX IF NOT EXISTS playback_event_recent
              ON playback_events(track_id, id DESC);
            CREATE TABLE IF NOT EXISTS widget_bindings(
              chat_id INTEGER NOT NULL,
              message_id INTEGER NOT NULL,
              track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
              expires_at INTEGER NOT NULL,
              PRIMARY KEY(chat_id, message_id));
            CREATE TABLE IF NOT EXISTS widget_receipts(
              query_id TEXT PRIMARY KEY,
              result TEXT NOT NULL,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE INDEX IF NOT EXISTS widget_expiry ON widget_bindings(expires_at);
            CREATE TABLE IF NOT EXISTS sent_history(
              id INTEGER PRIMARY KEY,
              queue_id INTEGER REFERENCES queues(id) ON DELETE SET NULL,
              track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
              sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE INDEX IF NOT EXISTS sent_history_order ON sent_history(id DESC);
            CREATE INDEX IF NOT EXISTS queue_item_order ON queue_items(queue_id, position);
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

    def get_track(self, track_id: int) -> Track | None:
        with self.con() as db:
            row = db.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()
            return self.track(row) if row is not None else None

    def all_tracks(self, limit=1000):
        with self.con() as db:
            return [self.track(r) for r in db.execute(
                "SELECT * FROM tracks ORDER BY id DESC LIMIT ?", (limit,))]

    def search_tracks(self, query: str, limit: int = 20):
        query = query.strip()
        if not query:
            return []
        # instr() is literal search: no accidental LIKE wildcard expansion.
        with self.con() as db:
            return [self.track(r) for r in db.execute(
                """SELECT * FROM tracks WHERE
                   instr(lower(title), lower(?)) > 0 OR instr(lower(artist), lower(?)) > 0
                   ORDER BY id DESC LIMIT ?""", (query, query, limit))]

    def liked_id(self):
        with self.con() as db:
            return db.execute("SELECT id FROM playlists WHERE kind='liked'").fetchone()[0]

    def playlists(self):
        with self.con() as db:
            return [dict(r) for r in db.execute(
                """SELECT p.id,p.name,p.kind,COUNT(pt.track_id) AS count
                   FROM playlists p LEFT JOIN playlist_tracks pt ON pt.playlist_id=p.id
                   GROUP BY p.id ORDER BY CASE WHEN p.kind='liked' THEN 0 ELSE 1 END,p.id""")]

    def get_playlist(self, playlist_id):
        with self.con() as db:
            row = db.execute(
                "SELECT id,name,kind FROM playlists WHERE id=?", (playlist_id,)
            ).fetchone()
            return dict(row) if row else None

    def create_playlist(self, name):
        name = name.strip()
        if not (1 <= len(name) <= 80) or name.casefold() == "мне нравится":
            raise ValueError("Invalid playlist name")
        with self.con() as db:
            return db.execute("INSERT INTO playlists(name,kind) VALUES(?,'custom')",
                              (name,)).lastrowid

    def rename_playlist(self, playlist_id, name):
        name = name.strip()
        if not (1 <= len(name) <= 80) or name.casefold() == "мне нравится":
            raise ValueError("Invalid playlist name")
        with self.con() as db:
            changed = db.execute(
                "UPDATE playlists SET name=? WHERE id=? AND kind='custom'",
                (name, playlist_id),
            ).rowcount
            if not changed:
                raise ValueError("Custom playlist not found")

    def delete_playlist(self, playlist_id):
        with self.con() as db:
            changed = db.execute(
                "DELETE FROM playlists WHERE id=? AND kind='custom'", (playlist_id,)
            ).rowcount
            if not changed:
                raise ValueError("Custom playlist not found")

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
        with self.con() as db:
            db.execute("DELETE FROM dislikes WHERE track_id=?", (track_id,))
        return self.add_to(self.liked_id(), track_id)

    def unlike(self, track_id):
        return self.remove_from(self.liked_id(), track_id)

    def playlist_tracks(self, playlist_id, limit=1000):
        with self.con() as db:
            return [self.track(r) for r in db.execute(
                """SELECT t.* FROM tracks t JOIN playlist_tracks p ON t.id=p.track_id
                   WHERE p.playlist_id=? ORDER BY p.position LIMIT ?""",
                (playlist_id,limit))]

    def liked_ids(self):
        return {t.id for t in self.playlist_tracks(self.liked_id())}

    @staticmethod
    def _rewrite_positions(db, playlist_id, ordered_ids):
        for position, track_id in enumerate(ordered_ids, start=1):
            db.execute(
                "UPDATE playlist_tracks SET position=? WHERE playlist_id=? AND track_id=?",
                (position, playlist_id, track_id),
            )

    def remove_from(self, playlist_id, track_id):
        with self.con() as db:
            deleted = db.execute(
                "DELETE FROM playlist_tracks WHERE playlist_id=? AND track_id=?",
                (playlist_id, track_id),
            ).rowcount
            if deleted:
                order = [r[0] for r in db.execute(
                    "SELECT track_id FROM playlist_tracks WHERE playlist_id=? ORDER BY position",
                    (playlist_id,),
                )]
                self._rewrite_positions(db, playlist_id, order)
            return bool(deleted)

    def move_track(self, playlist_id, track_id, new_position):
        with self.con() as db:
            if new_position < 1:
                raise ValueError("Position must be positive")
            order = [r[0] for r in db.execute(
                "SELECT track_id FROM playlist_tracks WHERE playlist_id=? ORDER BY position",
                (playlist_id,),
            )]
            if track_id not in order or new_position > len(order):
                raise ValueError("Track or position not found in playlist")
            order.remove(track_id)
            order.insert(new_position - 1, track_id)
            self._rewrite_positions(db, playlist_id, order)

    def dislike(self, track_id):
        with self.con() as db:
            if not db.execute("SELECT 1 FROM tracks WHERE id=?", (track_id,)).fetchone():
                raise ValueError("Track not found")
            db.execute("INSERT OR IGNORE INTO dislikes(track_id) VALUES(?)", (track_id,))
            # Don't leave the same song both liked and disliked.
            db.execute(
                "DELETE FROM playlist_tracks WHERE playlist_id=? AND track_id=?",
                (self.liked_id(), track_id),
            )

    def disliked_ids(self):
        with self.con() as db:
            return {r[0] for r in db.execute("SELECT track_id FROM dislikes")}

    def recent_ids(self, limit=80):
        with self.con() as db:
            return {int(r[0]) for r in db.execute(
                "SELECT track_id FROM sent_history ORDER BY id DESC LIMIT ?", (limit,)
            )}

    def create_queue(self, track_ids, label, source_kind):
        # Fixed snapshots: removing a track from a playlist doesn't silently
        # rewrite an already created listening queue.
        ids = list(dict.fromkeys(track_ids))
        if not ids:
            raise ValueError("Cannot create empty queue")
        with self.con() as db:
            cursor = db.execute(
                "INSERT INTO queues(label,source_kind) VALUES(?,?)",
                (label[:120], source_kind[:40]),
            )
            queue_id = cursor.lastrowid
            db.executemany(
                "INSERT INTO queue_items(queue_id,position,track_id) VALUES(?,?,?)",
                [(queue_id, i, t_id) for i, t_id in enumerate(ids)],
            )
            return queue_id

    def queue_kind(self, queue_id: int) -> str | None:
        with self.con() as db:
            row = db.execute("SELECT source_kind FROM queues WHERE id=?", (queue_id,)).fetchone()
            return str(row["source_kind"]) if row is not None else None

    def latest_sent_ids(self, limit: int = 3) -> set[int]:
        with self.con() as db:
            return {int(row[0]) for row in db.execute(
                "SELECT track_id FROM sent_history ORDER BY id DESC LIMIT ?", (limit,)
            )}

    def extend_exhausted_queue(self, queue_id: int, track_ids) -> bool:
        """Append fresh radio candidates atomically, only after previous batch is consumed.

        Existing explicit playlists are frozen snapshots and are not extendable.
        Repeats across future radio batches are allowed; duplicates within a batch
        are removed. The caller must recompute recommendations before invoking.
        """
        ids = list(dict.fromkeys(track_ids))
        if not ids:
            return False
        with self.con() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT source_kind, cursor FROM queues WHERE id=?", (queue_id,)
            ).fetchone()
            if row is None or row["source_kind"] not in ("balanced", "discover", "favorites"):
                return False
            total = int(db.execute(
                "SELECT COUNT(*) FROM queue_items WHERE queue_id=?", (queue_id,)
            ).fetchone()[0])
            if int(row["cursor"]) != total:
                return False
            db.executemany(
                "INSERT INTO queue_items(queue_id,position,track_id) VALUES(?,?,?)",
                [(queue_id, total + offset, track_id)
                 for offset, track_id in enumerate(ids)],
            )
            return True

    def peek_queue(self, queue_id, size=10):
        if not 1 <= size <= 10:
            raise ValueError("Telegram batch must contain 1..10 tracks")
        with self.con() as db:
            queue = db.execute(
                "SELECT id,label,cursor FROM queues WHERE id=?", (queue_id,)
            ).fetchone()
            if queue is None:
                raise ValueError("Queue not found")
            rows = db.execute(
                """SELECT t.* FROM queue_items qi JOIN tracks t ON t.id=qi.track_id
                   WHERE qi.queue_id=? AND qi.position>=?
                   ORDER BY qi.position LIMIT ?""",
                (queue_id, queue["cursor"], size),
            ).fetchall()
            total = db.execute(
                "SELECT COUNT(*) FROM queue_items WHERE queue_id=?", (queue_id,)
            ).fetchone()[0]
            tracks = tuple(self.track(row) for row in rows)
            return QueueBatch(queue_id, queue["cursor"], queue["label"],
                              tracks, max(0, total - queue["cursor"] - len(tracks)))

    def mark_queue_sent(self, batch):
        if not batch.tracks:
            return False
        with self.con() as db:
            # Compare-and-swap: clicking an old "more" button cannot replay
            # a batch once another callback has advanced the queue.
            changed = db.execute(
                "UPDATE queues SET cursor=cursor+? WHERE id=? AND cursor=?",
                (len(batch.tracks), batch.queue_id, batch.cursor),
            ).rowcount
            if not changed:
                return False
            db.executemany(
                "INSERT INTO sent_history(queue_id,track_id) VALUES(?,?)",
                [(batch.queue_id, t.id) for t in batch.tracks],
            )
            return True

    def set_rating(self, track_id: int, score: int) -> None:
        """Record an explicit 1–10 score. Rating does not modify favorites."""
        if type(score) is not int or not 1 <= score <= 10:
            raise ValueError("Rating must be an integer from 1 to 10")
        with self.con() as db:
            if not db.execute("SELECT 1 FROM tracks WHERE id=?", (track_id,)).fetchone():
                raise ValueError("Track not found")
            db.execute(
                """INSERT INTO track_ratings(track_id,score) VALUES (?,?)
                   ON CONFLICT(track_id) DO UPDATE SET
                     score=excluded.score, updated_at=CURRENT_TIMESTAMP""",
                (track_id, score),
            )

    def clear_rating(self, track_id: int) -> bool:
        with self.con() as db:
            return bool(db.execute(
                "DELETE FROM track_ratings WHERE track_id=?", (track_id,)
            ).rowcount)

    def ratings(self) -> dict[int, int]:
        with self.con() as db:
            return {int(r["track_id"]): int(r["score"]) for r in db.execute(
                "SELECT track_id, score FROM track_ratings"
            )}

    def record_playback_event(
        self, track_id: int, event: str, *, source: str = "manual",
        dedupe_key: str | None = None,
    ) -> bool:
        """Log an *explicit* signal. Telegram native playback emits no such event.

        'instrumented' is reserved for a future independently authenticated player.
        Returning False indicates an already-recorded dedupe_key.
        """
        if event not in {"skip", "complete"} or source not in {"manual", "instrumented"}:
            raise ValueError("Unsupported feedback signal or source")
        if dedupe_key is not None and not 1 <= len(dedupe_key) <= 160:
            raise ValueError("Invalid event idempotency key")
        with self.con() as db:
            if not db.execute("SELECT 1 FROM tracks WHERE id=?", (track_id,)).fetchone():
                raise ValueError("Track not found")
            return bool(db.execute(
                """INSERT OR IGNORE INTO playback_events(track_id,event,source,dedupe_key)
                   VALUES(?,?,?,?)""", (track_id, event, source, dedupe_key),
            ).rowcount)

    def playback_stats(self) -> dict[int, tuple[int, int]]:
        """Recent *reported* skips and completions; 20 events/song, 30-day window.

        Sent-history has no bearing on playback stats.
        """
        with self.con() as db:
            rows = db.execute("""
                WITH recent AS (
                  SELECT track_id, event,
                    ROW_NUMBER() OVER (PARTITION BY track_id ORDER BY id DESC) AS rank
                  FROM playback_events
                  WHERE created_at >= datetime('now','-30 days')
                )
                SELECT track_id,
                       SUM(CASE WHEN event='skip' THEN 1 ELSE 0 END) AS skipped,
                       SUM(CASE WHEN event='complete' THEN 1 ELSE 0 END) AS completed
                  FROM recent WHERE rank <= 20 GROUP BY track_id
            """).fetchall()
            return {int(r["track_id"]): (int(r["skipped"]), int(r["completed"]))
                    for r in rows}

    def bind_widget(self, chat_id: int, message_id: int, track_id: int,
                    *, now: int | None = None, lifetime: int = 604800) -> None:
        """A sent private-chat widget is bound to an actual existing track."""
        import time
        if chat_id <= 0 or message_id <= 0 or lifetime <= 0:
            raise ValueError("Invalid widget binding")
        current = int(time.time()) if now is None else now
        with self.con() as db:
            if not db.execute("SELECT 1 FROM tracks WHERE id=?", (track_id,)).fetchone():
                raise ValueError("Track not found")
            db.execute(
                """INSERT INTO widget_bindings(chat_id,message_id,track_id,expires_at)
                   VALUES(?,?,?,?)
                   ON CONFLICT(chat_id,message_id) DO UPDATE SET
                     track_id=excluded.track_id, expires_at=excluded.expires_at""",
                (chat_id, message_id, track_id, current + lifetime),
            )

    def execute_widget_command(self, chat_id: int, message_id: int,
                               query_id: str, command, *, now: int | None = None) -> str:
        """Atomic action and receipt for a trusted, already authenticated query.

        Runtime integration is feature-gated: this function does not receive
        network events by itself and cannot make HTML widgets available.
        """
        import time
        if not isinstance(query_id, str) or not 1 <= len(query_id) <= 128:
            raise ValueError("Invalid query identifier")
        current = int(time.time()) if now is None else now
        with self.con() as db:
            db.execute("BEGIN IMMEDIATE")
            received = db.execute(
                "SELECT result FROM widget_receipts WHERE query_id=?", (query_id,)
            ).fetchone()
            if received:
                return received["result"]
            binding = db.execute(
                """SELECT track_id FROM widget_bindings
                   WHERE chat_id=? AND message_id=? AND expires_at>?""",
                (chat_id, message_id, current),
            ).fetchone()
            if binding is None:
                result = "Карточка недоступна или устарела"
            else:
                track_id = int(binding["track_id"])
                if command.action == "rate" and type(command.score) is int and 1 <= command.score <= 10:
                    db.execute(
                        """INSERT INTO track_ratings(track_id,score) VALUES(?,?)
                           ON CONFLICT(track_id) DO UPDATE SET
                             score=excluded.score, updated_at=CURRENT_TIMESTAMP""",
                        (track_id, command.score),
                    )
                    result = f"Оценка {command.score}/10 сохранена"
                elif command.action in {"skip", "complete"}:
                    db.execute(
                        """INSERT OR IGNORE INTO playback_events(track_id,event,source,dedupe_key)
                           VALUES(?,?,'manual',?)""",
                        (track_id, command.action, "widget:" + query_id),
                    )
                    result = ("⏭ Пропуск отмечен вручную" if command.action == "skip"
                              else "✅ Дослушивание отмечено вручную")
                else:
                    result = "Недопустимое действие"
            db.execute(
                "INSERT INTO widget_receipts(query_id,result) VALUES(?,?)",
                (query_id, result),
            )
            return result
