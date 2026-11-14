"""SQLite storage for an owner-only Telegram audio library.

No media binaries or remote URLs are stored here; only Telegram file IDs.
Existing v0.1 databases are upgraded additively with CREATE IF NOT EXISTS.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
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

    @contextmanager
    def con(self):
        """Commit or roll back, and *close* the SQLite descriptor every time.

        sqlite3.Connection used directly as a context manager does not close
        itself on exit; the old implementation leaked file descriptors during
        repeated bot/API requests.
        """
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=10000")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def init(self):
        with self.con() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS screen_titles (
              media_type TEXT NOT NULL CHECK(media_type IN ('movie','tv')),
              tmdb_id INTEGER NOT NULL, title TEXT NOT NULL,
              original_title TEXT, release_year INTEGER,
              description TEXT, average_score REAL,
              last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(media_type,tmdb_id));
            CREATE TABLE IF NOT EXISTS screen_watchlist (
              media_type TEXT NOT NULL, tmdb_id INTEGER NOT NULL,
              watch_status TEXT NOT NULL CHECK (
                watch_status IN ('planned','watching','completed','on_hold','dropped')),
              season INTEGER NOT NULL DEFAULT 0 CHECK(season>=0),
              episode INTEGER NOT NULL DEFAULT 0 CHECK(episode>=0),
              score INTEGER CHECK(score BETWEEN 1 AND 10),
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(media_type,tmdb_id),
              FOREIGN KEY(media_type,tmdb_id)
                REFERENCES screen_titles(media_type,tmdb_id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS anime_titles (
              id INTEGER PRIMARY KEY, title TEXT NOT NULL,
              english TEXT, native TEXT, episodes INTEGER,
              format TEXT, release_status TEXT, year INTEGER,
              average_score INTEGER, mal_id INTEGER,
              last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS anime_watchlist (
              anime_id INTEGER PRIMARY KEY REFERENCES anime_titles(id) ON DELETE CASCADE,
              watch_status TEXT NOT NULL CHECK (
                watch_status IN ('planned','watching','completed','on_hold','dropped')),
              progress INTEGER NOT NULL DEFAULT 0 CHECK (progress>=0),
              score INTEGER CHECK (score BETWEEN 1 AND 10),
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS media_import_leases (
              source_type TEXT NOT NULL,
              source_key TEXT NOT NULL,
              lease_token TEXT NOT NULL,
              expires_at INTEGER NOT NULL,
              PRIMARY KEY(source_type, source_key));
            CREATE INDEX IF NOT EXISTS media_import_leases_expiry
              ON media_import_leases(expires_at);
            CREATE TABLE IF NOT EXISTS youtube_candidates (
              video_id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              channel TEXT NOT NULL,
              duration_sec INTEGER,
              last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS youtube_audio_assets (
              video_id TEXT PRIMARY KEY REFERENCES youtube_candidates(video_id) ON DELETE CASCADE,
              track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
              linked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS catalog_candidates (
              mbid TEXT PRIMARY KEY, title TEXT NOT NULL, artist TEXT NOT NULL,
              artist_mbid TEXT, duration_ms INTEGER, variant INTEGER NOT NULL DEFAULT 0,
              last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS discovery_wishlist (
              mbid TEXT PRIMARY KEY REFERENCES catalog_candidates(mbid) ON DELETE CASCADE,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS catalog_audio_assets (
              mbid TEXT PRIMARY KEY REFERENCES catalog_candidates(mbid) ON DELETE CASCADE,
              track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
              linked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS tracks(
              id INTEGER PRIMARY KEY, file_id TEXT NOT NULL,
              file_unique_id TEXT NOT NULL UNIQUE, title TEXT NOT NULL, artist TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS track_recording_links(
              track_id INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
              mbid TEXT NOT NULL REFERENCES catalog_candidates(mbid) ON DELETE CASCADE,
              provenance TEXT NOT NULL CHECK(provenance IN ('confirmed','import')),
              linked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE INDEX IF NOT EXISTS track_recording_by_mbid
              ON track_recording_links(mbid);
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
            # Existing imported audio rows predate recording-identity links.
            db.execute(
                """INSERT OR IGNORE INTO track_recording_links(track_id,mbid,provenance)
                   SELECT track_id,mbid,'import' FROM catalog_audio_assets
                   ORDER BY linked_at DESC"""
            )

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

    def latest_queue_id(self) -> int | None:
        """Return the latest persisted listening queue (owner-only bot)."""
        with self.con() as db:
            row = db.execute(
                "SELECT id FROM queues ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return int(row["id"]) if row is not None else None

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

    def record_single_send(self, track_id: int) -> None:
        """Record a confirmed Telegram delivery, not a real playback event."""
        with self.con() as db:
            if not db.execute(
                "SELECT 1 FROM tracks WHERE id=?", (track_id,)
            ).fetchone():
                raise ValueError("Track not found")
            db.execute(
                "INSERT INTO sent_history(queue_id,track_id) VALUES(NULL,?)",
                (track_id,),
            )

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

    def remember_candidates(self, candidates) -> None:
        """Catalog metadata only: no Telegram file_id, no playable asset implied."""
        with self.con() as db:
            for t in candidates:
                db.execute(
                    """INSERT INTO catalog_candidates
                       (mbid,title,artist,artist_mbid,duration_ms,variant)
                       VALUES(?,?,?,?,?,?)
                       ON CONFLICT(mbid) DO UPDATE SET
                         title=excluded.title,artist=excluded.artist,
                         artist_mbid=excluded.artist_mbid,
                         duration_ms=excluded.duration_ms,variant=excluded.variant,
                         last_seen_at=CURRENT_TIMESTAMP""",
                    (t.mbid, t.title, t.artist, t.artist_mbid,
                     t.duration_ms, int(t.variant)),
                )

    def wishlist_add(self, mbid: str) -> bool:
        """Only allow saving a recording already resolved by trusted catalog API."""
        with self.con() as db:
            exists = db.execute(
                "SELECT 1 FROM catalog_candidates WHERE mbid=?", (mbid,)
            ).fetchone()
            if not exists:
                raise ValueError("Recording not found in discovered catalog")
            return bool(db.execute(
                "INSERT OR IGNORE INTO discovery_wishlist(mbid) VALUES(?)", (mbid,)
            ).rowcount)

    def wishlist_remove(self, mbid: str) -> bool:
        with self.con() as db:
            return bool(db.execute(
                "DELETE FROM discovery_wishlist WHERE mbid=?", (mbid,)
            ).rowcount)

    def wishlist(self, limit: int = 100) -> list[dict]:
        with self.con() as db:
            return [dict(r) for r in db.execute(
                """SELECT c.mbid,c.title,c.artist,c.artist_mbid,c.duration_ms,
                          c.variant,w.created_at
                   FROM discovery_wishlist w
                   JOIN catalog_candidates c ON c.mbid=w.mbid
                   ORDER BY w.created_at DESC, w.mbid LIMIT ?""", (limit,)
            )]

    def wishlist_ids(self) -> set[str]:
        with self.con() as db:
            return {str(r[0]) for r in db.execute(
                "SELECT mbid FROM discovery_wishlist"
            )}

    def get_candidate(self, mbid: str) -> dict | None:
        with self.con() as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                """SELECT c.mbid,c.title,c.artist,c.artist_mbid,c.duration_ms,
                          c.variant,a.track_id
                   FROM catalog_candidates c
                   LEFT JOIN catalog_audio_assets a ON a.mbid=c.mbid
                   WHERE c.mbid=?""", (mbid,)
            ).fetchone()
            return dict(row) if row is not None else None

    def link_candidate_track(self, mbid: str, track_id: int) -> None:
        """The successful Telegram upload path binds both asset and recording identity."""
        with self.con() as db:
            if not db.execute("SELECT 1 FROM catalog_candidates WHERE mbid=?", (mbid,)).fetchone():
                raise ValueError("Unknown catalog recording")
            if not db.execute("SELECT 1 FROM tracks WHERE id=?", (track_id,)).fetchone():
                raise ValueError("Unknown Telegram audio track")
            db.execute(
                """INSERT INTO catalog_audio_assets(mbid,track_id) VALUES(?,?)
                   ON CONFLICT(mbid) DO UPDATE SET
                     track_id=excluded.track_id, linked_at=CURRENT_TIMESTAMP""",
                (mbid, track_id),
            )
            db.execute(
                """INSERT INTO track_recording_links(track_id,mbid,provenance)
                   VALUES(?,?,'import')
                   ON CONFLICT(track_id) DO UPDATE SET
                     mbid=excluded.mbid, provenance='import',
                     linked_at=CURRENT_TIMESTAMP""",
                (track_id, mbid),
            )

    def linked_candidate_tracks(self) -> dict[str, int]:
        with self.con() as db:
            return {str(row[0]): int(row[1]) for row in db.execute(
                "SELECT mbid,track_id FROM catalog_audio_assets"
            )}

    def confirm_recording_match(self, track_id: int, mbid: str) -> dict:
        """Owner-confirmed identity for existing Telegram files.

        This is NEVER inferred from title matching. Imported/authoritative links
        are immutable here. Candidates must already exist in the trusted cache.
        """
        with self.con() as db:
            if not db.execute("SELECT 1 FROM tracks WHERE id=?", (track_id,)).fetchone():
                raise ValueError("Track not found")
            candidate = db.execute(
                "SELECT mbid,title,artist,artist_mbid FROM catalog_candidates WHERE mbid=?",
                (mbid,),
            ).fetchone()
            if candidate is None:
                raise ValueError("Recording not found in catalog")
            current = db.execute(
                "SELECT provenance FROM track_recording_links WHERE track_id=?",
                (track_id,),
            ).fetchone()
            if current is not None and current["provenance"] == "import":
                raise ValueError("Imported recording links cannot be reassigned manually")
            db.execute(
                """INSERT INTO track_recording_links(track_id,mbid,provenance)
                   VALUES(?,?,'confirmed')
                   ON CONFLICT(track_id) DO UPDATE SET
                    mbid=excluded.mbid, provenance='confirmed',
                    linked_at=CURRENT_TIMESTAMP""",
                (track_id, mbid),
            )
            return dict(candidate)

    def unlink_recording_match(self, track_id: int) -> bool:
        """Remove only manually confirmed identity, never an imported asset."""
        with self.con() as db:
            return bool(db.execute(
                """DELETE FROM track_recording_links
                   WHERE track_id=? AND provenance='confirmed'""",
                (track_id,),
            ).rowcount)

    def recording_for_track(self, track_id: int) -> dict | None:
        with self.con() as db:
            row = db.execute(
                """SELECT l.track_id,l.mbid,l.provenance,c.title,c.artist,
                          c.artist_mbid,c.duration_ms,c.variant
                   FROM track_recording_links l
                   JOIN catalog_candidates c ON c.mbid=l.mbid
                   WHERE l.track_id=?""", (track_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def recording_for_tracks(self) -> dict[int, dict]:
        with self.con() as db:
            return {int(row["track_id"]): dict(row) for row in db.execute(
                """SELECT l.track_id,l.mbid,l.provenance,c.title,c.artist,
                          c.artist_mbid,c.duration_ms,c.variant
                   FROM track_recording_links l
                   JOIN catalog_candidates c ON c.mbid=l.mbid"""
            )}

    def confirmed_recording_ids(self) -> set[str]:
        with self.con() as db:
            return {str(row[0]) for row in db.execute(
                "SELECT mbid FROM track_recording_links"
            )}

    def resolved_catalog_tracks(self) -> dict[str, int]:
        """Explicit MBID associations, imported or manually confirmed only."""
        resolved = self.linked_candidate_tracks()
        for track_id, record in self.recording_for_tracks().items():
            resolved[record["mbid"]] = track_id
        return resolved

    def remember_youtube_videos(self, videos) -> None:
        """Persist only trusted video IDs and plain-text metadata, never URLs."""
        with self.con() as db:
            for v in videos:
                db.execute(
                    """INSERT INTO youtube_candidates(video_id,title,channel,duration_sec)
                       VALUES(?,?,?,?)
                       ON CONFLICT(video_id) DO UPDATE SET
                       title=excluded.title,channel=excluded.channel,
                       duration_sec=excluded.duration_sec,
                       last_seen_at=CURRENT_TIMESTAMP""",
                    (v.video_id, v.title[:220], v.channel[:150], v.duration_sec),
                )

    def get_youtube_video(self, video_id: str) -> dict | None:
        with self.con() as db:
            row = db.execute(
                """SELECT v.video_id,v.title,v.channel,v.duration_sec,a.track_id
                   FROM youtube_candidates v
                   LEFT JOIN youtube_audio_assets a ON a.video_id=v.video_id
                   WHERE v.video_id=?""", (video_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def link_youtube_video(self, video_id: str, track_id: int) -> None:
        """Called only after Telegram returned an actual audio file_id."""
        with self.con() as db:
            if not db.execute(
                "SELECT 1 FROM youtube_candidates WHERE video_id=?", (video_id,)
            ).fetchone():
                raise ValueError("Video was not searched through the bot")
            if not db.execute(
                "SELECT 1 FROM tracks WHERE id=?", (track_id,)
            ).fetchone():
                raise ValueError("Telegram track not found")
            db.execute(
                """INSERT INTO youtube_audio_assets(video_id,track_id) VALUES(?,?)
                   ON CONFLICT(video_id) DO UPDATE SET
                    track_id=excluded.track_id,linked_at=CURRENT_TIMESTAMP""",
                (video_id,track_id),
            )

    def associate_youtube_recording(self, video_id: str, mbid: str) -> int:
        """Bind an explicitly selected YouTube audio to its catalog recording.

        This must never be called merely because title/artist text looks
        similar. Both catalog-to-file and track-to-catalog links are written
        in one transaction, rejecting conflicting identities.
        """
        with self.con() as db:
            db.execute("BEGIN IMMEDIATE")
            candidate = db.execute(
                "SELECT mbid,title,artist FROM catalog_candidates WHERE mbid=?",
                (mbid,),
            ).fetchone()
            video = db.execute(
                """SELECT a.track_id, v.title AS video_title,
                          v.channel AS video_channel,
                          t.title AS track_title,t.artist AS track_artist
                   FROM youtube_audio_assets a
                   JOIN youtube_candidates v ON v.video_id=a.video_id
                   JOIN tracks t ON t.id=a.track_id
                   WHERE a.video_id=?""", (video_id,)
            ).fetchone()
            if candidate is None or video is None:
                raise ValueError("Catalog recording or uploaded video missing")
            track_id = int(video["track_id"])
            prior_identity = db.execute(
                "SELECT mbid FROM track_recording_links WHERE track_id=?",
                (track_id,)
            ).fetchone()
            prior_asset = db.execute(
                "SELECT track_id FROM catalog_audio_assets WHERE mbid=?",
                (mbid,)
            ).fetchone()
            if prior_identity is not None and prior_identity["mbid"] != mbid:
                raise ValueError("This audio is already linked to another recording")
            if prior_asset is not None and int(prior_asset["track_id"]) != track_id:
                raise ValueError("This recording already has another Telegram audio")
            db.execute(
                """INSERT INTO catalog_audio_assets(mbid,track_id) VALUES(?,?)
                   ON CONFLICT(mbid) DO UPDATE SET
                   track_id=excluded.track_id,linked_at=CURRENT_TIMESTAMP""",
                (mbid, track_id),
            )
            db.execute(
                """INSERT INTO track_recording_links(track_id,mbid,provenance)
                   VALUES(?,?,'import')
                   ON CONFLICT(track_id) DO UPDATE SET
                   provenance='import',linked_at=CURRENT_TIMESTAMP""",
                (track_id, mbid),
            )
            # A plain /yt import initially uses the YouTube channel and
            # video title. On later *explicit* identification, promote only
            # untouched metadata, never overwrite user-edited names.
            if (video["track_title"] == video["video_title"] and
                    video["track_artist"] == video["video_channel"]):
                db.execute(
                    "UPDATE tracks SET title=?,artist=? WHERE id=?",
                    (candidate["title"][:250], candidate["artist"][:250],
                     track_id),
                )
            return track_id

    def remember_anime(self, entries) -> None:
        """Only AniList catalog metadata, never video streaming links."""
        with self.con() as db:
            db.executemany(
                """INSERT INTO anime_titles(
                   id,title,english,native,episodes,format,release_status,year,
                   average_score,mal_id) VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     title=excluded.title,english=excluded.english,
                     native=excluded.native,episodes=excluded.episodes,
                     format=excluded.format,release_status=excluded.release_status,
                     year=excluded.year,average_score=excluded.average_score,
                     mal_id=excluded.mal_id,last_seen_at=CURRENT_TIMESTAMP""",
                [(t.id,t.title,t.english,t.native,t.episodes,t.format,
                  t.release_status,t.year,t.average_score,t.mal_id)
                 for t in entries],
            )

    def get_anime(self, anime_id: int) -> dict | None:
        with self.con() as db:
            row = db.execute(
                """SELECT t.*, w.watch_status,w.progress,w.score
                   FROM anime_titles t LEFT JOIN anime_watchlist w ON w.anime_id=t.id
                   WHERE t.id=?""", (anime_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def anime_list(self, limit: int = 200) -> list[dict]:
        with self.con() as db:
            return [dict(r) for r in db.execute(
                """SELECT t.id,t.title,t.english,t.native,t.episodes,t.format,
                          t.release_status,t.year,t.average_score,t.mal_id,
                          w.watch_status,w.progress,w.score
                   FROM anime_watchlist w JOIN anime_titles t ON t.id=w.anime_id
                   ORDER BY w.updated_at DESC,t.id DESC LIMIT ?""",
                (limit,),
            )]

    def update_anime_watch(self, anime_id: int, *,
                           watch_status: str | None = None,
                           progress: int | None = None,
                           score: int | None = None) -> dict:
        """Atomic owner-only list update, validating progress against episodes."""
        states = {"planned", "watching", "completed", "on_hold", "dropped"}
        if watch_status is not None and watch_status not in states:
            raise ValueError("Unknown watch status")
        if progress is not None and (type(progress) is not int or not 0 <= progress <= 10000):
            raise ValueError("Progress must be 0..10000")
        if score is not None and (type(score) is not int or not 1 <= score <= 10):
            raise ValueError("Score must be 1..10")
        with self.con() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                """SELECT t.id,t.episodes,w.watch_status,w.progress,w.score
                   FROM anime_titles t LEFT JOIN anime_watchlist w ON w.anime_id=t.id
                   WHERE t.id=?""", (anime_id,),
            ).fetchone()
            if current is None:
                raise ValueError("Anime must be found in AniList first")
            status = watch_status or current["watch_status"] or "planned"
            next_progress = progress if progress is not None else (current["progress"] or 0)
            total = current["episodes"]
            if total is not None and next_progress > total:
                raise ValueError("Progress exceeds the number of episodes")
            next_score = score if score is not None else current["score"]
            db.execute(
                """INSERT INTO anime_watchlist(anime_id,watch_status,progress,score)
                   VALUES(?,?,?,?) ON CONFLICT(anime_id) DO UPDATE SET
                    watch_status=excluded.watch_status,progress=excluded.progress,
                    score=excluded.score,updated_at=CURRENT_TIMESTAMP""",
                (anime_id,status,next_progress,next_score),
            )
            return dict(db.execute(
                "SELECT anime_id,watch_status,progress,score FROM anime_watchlist WHERE anime_id=?",
                (anime_id,),
            ).fetchone())

    def remove_anime_watch(self, anime_id: int) -> bool:
        with self.con() as db:
            return bool(db.execute(
                "DELETE FROM anime_watchlist WHERE anime_id=?", (anime_id,),
            ).rowcount)

    def remember_screen_titles(self, items) -> None:
        """Only TMDB metadata without streaming links or credentials."""
        with self.con() as db:
            db.executemany(
                """INSERT INTO screen_titles(media_type,tmdb_id,title,original_title,
                   release_year,description,average_score)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(media_type,tmdb_id) DO UPDATE SET
                    title=excluded.title,original_title=excluded.original_title,
                    release_year=excluded.release_year,
                    description=excluded.description,average_score=excluded.average_score,
                    last_seen_at=CURRENT_TIMESTAMP""",
                [(t.media_type,t.id,t.title,t.original_title,t.release_year,
                  t.description,t.average_score) for t in items],
            )

    def screen_watchlist(self, limit: int = 200) -> list[dict]:
        with self.con() as db:
            return [dict(r) for r in db.execute(
                """SELECT t.media_type,t.tmdb_id,t.title,t.original_title,
                          t.release_year,t.description,t.average_score,
                          w.watch_status,w.season,w.episode,w.score
                   FROM screen_watchlist w JOIN screen_titles t
                     ON t.media_type=w.media_type AND t.tmdb_id=w.tmdb_id
                   ORDER BY w.updated_at DESC LIMIT ?""", (limit,),
            )]

    def update_screen_watch(self, media_type: str, tmdb_id: int, *,
                            watch_status: str, season: int = 0, episode: int = 0,
                            score: int | None = None) -> dict:
        if media_type not in {"movie","tv"} or type(tmdb_id) is not int or tmdb_id<=0:
            raise ValueError("Invalid TMDB title")
        if watch_status not in {"planned","watching","completed","on_hold","dropped"}:
            raise ValueError("Invalid watch status")
        if (type(season) is not int or type(episode) is not int
                or not 0 <= season <= 1000 or not 0 <= episode <= 10000):
            raise ValueError("Invalid season or episode")
        if media_type == "movie" and (season != 0 or episode != 0):
            raise ValueError("Movies have no season/episode progress")
        if score is not None and (type(score) is not int or not 1 <= score <= 10):
            raise ValueError("Score must be 1..10")
        with self.con() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute(
                "SELECT 1 FROM screen_titles WHERE media_type=? AND tmdb_id=?",
                (media_type,tmdb_id),
            ).fetchone():
                raise ValueError("Find the title in TMDB first")
            db.execute(
                """INSERT INTO screen_watchlist
                     (media_type,tmdb_id,watch_status,season,episode,score)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(media_type,tmdb_id) DO UPDATE SET
                     watch_status=excluded.watch_status,season=excluded.season,
                     episode=excluded.episode,score=excluded.score,
                     updated_at=CURRENT_TIMESTAMP""",
                (media_type,tmdb_id,watch_status,season,episode,score),
            )
            return dict(db.execute(
                """SELECT media_type,tmdb_id,watch_status,season,episode,score
                   FROM screen_watchlist WHERE media_type=? AND tmdb_id=?""",
                (media_type,tmdb_id),
            ).fetchone())

    def remove_screen_watch(self, media_type: str, tmdb_id: int) -> bool:
        with self.con() as db:
            return bool(db.execute(
                "DELETE FROM screen_watchlist WHERE media_type=? AND tmdb_id=?",
                (media_type,tmdb_id),
            ).rowcount)

    def acquire_media_import(self, kind: str, key: str, *, ttl: int = 900,
                             now: int | None = None) -> str | None:
        """Cross-process per-recording lease; return owned token or None.

        A crash leaves a bounded lease, not a permanent lock. The token
        prevents an expired worker from deleting a newer worker's claim.
        """
        import secrets
        import time
        if kind not in {"youtube", "musicbrainz", "global", "queue"}:
            raise ValueError("Unknown import provider")
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise ValueError("Invalid source identifier")
        if not 1 <= ttl <= 3600:
            raise ValueError("Invalid import lease TTL")
        timestamp = int(time.time()) if now is None else now
        token = secrets.token_urlsafe(16)
        with self.con() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "DELETE FROM media_import_leases WHERE expires_at<=?",
                (timestamp,),
            )
            claim = db.execute(
                """INSERT OR IGNORE INTO media_import_leases
                   (source_type,source_key,lease_token,expires_at)
                   VALUES(?,?,?,?)""",
                (kind, key, token, timestamp + ttl),
            )
            return token if claim.rowcount == 1 else None

    def release_media_import(self, kind: str, key: str, token: str) -> bool:
        with self.con() as db:
            return bool(db.execute(
                """DELETE FROM media_import_leases
                   WHERE source_type=? AND source_key=? AND lease_token=?""",
                (kind, key, token),
            ).rowcount)
