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
              file_unique_id TEXT NOT NULL UNIQUE, title TEXT NOT NULL, artist TEXT NOT NULL,
              added_at TEXT);
            CREATE TABLE IF NOT EXISTS audio_fingerprints (
              track_id INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
              fingerprint_sha256 TEXT NOT NULL,
              duration_sec INTEGER NOT NULL CHECK(duration_sec>0),
              checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE INDEX IF NOT EXISTS fingerprint_hash_index
              ON audio_fingerprints(fingerprint_sha256,duration_sec);
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
            CREATE TABLE IF NOT EXISTS track_verdicts(
              track_id INTEGER PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
              status TEXT NOT NULL CHECK(status IN ('maybe','add','never','later','interesting')),
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS catalog_verdicts(
              mbid TEXT PRIMARY KEY REFERENCES catalog_candidates(mbid) ON DELETE CASCADE,
              status TEXT NOT NULL CHECK(status IN ('maybe','add','never','later','interesting')),
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE INDEX IF NOT EXISTS track_verdict_status ON track_verdicts(status,updated_at);
            CREATE INDEX IF NOT EXISTS catalog_verdict_status ON catalog_verdicts(status,updated_at);
            CREATE TABLE IF NOT EXISTS taste_sources(
              provider TEXT PRIMARY KEY CHECK(provider IN ('yandex','youtube_music')),
              public_url TEXT,
              is_complete INTEGER NOT NULL DEFAULT 0,
              last_synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS taste_tracks(
              provider TEXT NOT NULL REFERENCES taste_sources(provider) ON DELETE CASCADE,
              external_id TEXT NOT NULL, title TEXT NOT NULL, artist TEXT NOT NULL,
              source_url TEXT NOT NULL, liked_at TEXT,
              normalized_title TEXT NOT NULL, normalized_artist TEXT NOT NULL,
              active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
              last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(provider,external_id));
            CREATE INDEX IF NOT EXISTS taste_tracks_active
              ON taste_tracks(provider,active,normalized_artist);
            CREATE TABLE IF NOT EXISTS weekly_digest_receipts (
              week_key TEXT PRIMARY KEY,
              sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS library_preferences (
              pref_key TEXT PRIMARY KEY,
              pref_value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS queues(
              id INTEGER PRIMARY KEY,
              label TEXT NOT NULL,
              source_kind TEXT NOT NULL,
              cursor INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS queue_filters (
              queue_id INTEGER PRIMARY KEY REFERENCES queues(id) ON DELETE CASCADE,
              min_rating INTEGER NOT NULL DEFAULT 0 CHECK(min_rating BETWEEN 0 AND 10),
              liked_only INTEGER NOT NULL DEFAULT 0 CHECK(liked_only IN (0,1)),
              no_repeat_today INTEGER NOT NULL DEFAULT 0 CHECK(no_repeat_today IN (0,1)));
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
            # Legacy rows do not have an original upload timestamp. Keep NULL
            # rather than inventing one at migration time.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(tracks)")}
            if "added_at" not in columns:
                db.execute("ALTER TABLE tracks ADD COLUMN added_at TEXT")
            db.execute("INSERT OR IGNORE INTO playlists(name,kind) VALUES('Мне нравится','liked')")
            # Existing explicit likes and dislikes are already decisions; carry
            # them forward without fabricating ratings or playback events.
            db.execute(
                """INSERT OR IGNORE INTO track_verdicts(track_id,status)
                   SELECT track_id,'add' FROM playlist_tracks
                   WHERE playlist_id=(SELECT id FROM playlists WHERE kind='liked')"""
            )
            db.execute(
                """INSERT OR IGNORE INTO track_verdicts(track_id,status)
                   SELECT track_id,'never' FROM dislikes"""
            )
            # Keep the old liked/disliked tables coherent with the explicit
            # verdict after upgrades from earlier bot versions.
            liked_id = db.execute(
                "SELECT id FROM playlists WHERE kind='liked'"
            ).fetchone()[0]
            db.execute(
                """DELETE FROM playlist_tracks WHERE playlist_id=? AND track_id IN (
                       SELECT track_id FROM track_verdicts WHERE status<>'add')""",
                (liked_id,),
            )
            db.execute(
                """DELETE FROM dislikes WHERE track_id IN (
                       SELECT track_id FROM track_verdicts WHERE status<>'never')"""
            )
            db.execute(
                """INSERT OR IGNORE INTO playlist_tracks(playlist_id,track_id,position)
                       SELECT ?,v.track_id,
                         (SELECT COALESCE(MAX(position),0) FROM playlist_tracks
                          WHERE playlist_id=?) + ROW_NUMBER() OVER (ORDER BY v.track_id)
                       FROM track_verdicts v WHERE v.status='add'""",
                (liked_id,liked_id),
            )
            db.execute(
                """INSERT OR IGNORE INTO dislikes(track_id)
                       SELECT track_id FROM track_verdicts WHERE status='never'"""
            )
            # Existing imported audio rows predate recording-identity links.
            db.execute(
                """INSERT OR IGNORE INTO track_recording_links(track_id,mbid,provenance)
                   SELECT track_id,mbid,'import' FROM catalog_audio_assets
                   ORDER BY linked_at DESC"""
            )
            # Repair pre-upgrade links where the catalog decision was already
            # chosen before a Telegram audio identity was confirmed.
            pending_reconcile = db.execute(
                """SELECT l.track_id,l.mbid FROM track_recording_links l
                   JOIN catalog_verdicts c ON c.mbid=l.mbid
                   LEFT JOIN track_verdicts t ON t.track_id=l.track_id
                   WHERE t.track_id IS NULL OR t.status<>c.status"""
            ).fetchall()
            for linked in pending_reconcile:
                self._reconcile_recording_verdict(
                    db, int(linked["track_id"]), str(linked["mbid"])
                )

    @staticmethod
    def track(row):
        return Track(row["id"], row["file_id"], row["file_unique_id"],
                     row["title"], row["artist"])

    def add_track(self, file_id: str, unique_id: str, title: str, artist: str) -> Track:
        if not file_id or not unique_id:
            raise ValueError("Missing Telegram file identifiers")
        with self.con() as db:
            db.execute("""INSERT INTO tracks(file_id,file_unique_id,title,artist,added_at)
                          VALUES(?,?,?,?,CURRENT_TIMESTAMP)
                          ON CONFLICT(file_unique_id) DO UPDATE SET file_id=excluded.file_id""",
                       (file_id, unique_id, title[:250], artist[:250]))
            row = db.execute("SELECT * FROM tracks WHERE file_unique_id=?", (unique_id,)).fetchone()
            return self.track(row)

    def apply_confirmed_recording_tags(self, track_id: int) -> Track:
        """Promote confirmed MusicBrainz metadata into the local library.

        Does not rewrite Telegram audio bytes, embedded MP3/M4A ID3 tags,
        or the file_id. Works only after explicit owner confirmation/import.
        """
        if type(track_id) is not int or track_id <= 0:
            raise ValueError("Invalid track ID")
        with self.con() as db:
            db.execute("BEGIN IMMEDIATE")
            record = db.execute(
                """SELECT c.title,c.artist FROM track_recording_links l
                   JOIN catalog_candidates c ON c.mbid=l.mbid
                   WHERE l.track_id=?""", (track_id,),
            ).fetchone()
            if record is None:
                raise ValueError("Confirm the MusicBrainz recording first")
            db.execute(
                "UPDATE tracks SET title=?,artist=? WHERE id=?",
                (record["title"][:250],record["artist"][:250],track_id),
            )
            row = db.execute(
                "SELECT * FROM tracks WHERE id=?", (track_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Track not found")
            return self.track(row)

    def save_audio_fingerprint(self, track_id: int, digest: str,
                               duration_sec: int) -> list[int]:
        """Store only SHA256 of fingerprint; report exact same-audio candidates.

        Never merge tracks automatically. Different encodings/lengths can
        still produce distinct hashes despite sounding identical.
        """
        import re
        if (not isinstance(digest, str) or
                re.fullmatch(r"[0-9a-f]{64}",digest) is None or
                type(duration_sec) is not int or not 1<=duration_sec<=86400):
            raise ValueError("Invalid audio fingerprint")
        with self.con() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM tracks WHERE id=?",
                          (track_id,)).fetchone() is None:
                raise ValueError("Track not found")
            duplicates = [int(r[0]) for r in db.execute(
                """SELECT track_id FROM audio_fingerprints
                   WHERE fingerprint_sha256=?
                     AND abs(duration_sec-?)<=2 AND track_id<>?
                   ORDER BY track_id LIMIT 20""",
                (digest,duration_sec,track_id),
            )]
            db.execute(
                """INSERT INTO audio_fingerprints
                   (track_id,fingerprint_sha256,duration_sec)
                   VALUES(?,?,?) ON CONFLICT(track_id) DO UPDATE SET
                   fingerprint_sha256=excluded.fingerprint_sha256,
                   duration_sec=excluded.duration_sec,
                   checked_at=CURRENT_TIMESTAMP""",
                (track_id,digest,duration_sec),
            )
            return duplicates

    def get_track(self, track_id: int) -> Track | None:
        with self.con() as db:
            row = db.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()
            return self.track(row) if row is not None else None

    def all_tracks(self, limit=1000):
        with self.con() as db:
            return [self.track(r) for r in db.execute(
                "SELECT * FROM tracks ORDER BY id DESC LIMIT ?", (limit,))]

    def search_tracks(self, query: str, limit: int = 20):
        query = query.strip().casefold()
        if not query:
            return []
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid search limit")
        found = []
        with self.con() as db:
            # Personal library; streaming rows avoids full result materialization.
            for row in db.execute("SELECT * FROM tracks ORDER BY id DESC"):
                if query in row["title"].casefold() or query in row["artist"].casefold():
                    found.append(self.track(row))
                    if len(found) >= limit:
                        break
        return found

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

    @staticmethod
    def _apply_verdict(db, track_id: int, status: str) -> None:
        """One atomic write path for the verdict, likes, dislikes and linked MBID."""
        db.execute(
            """INSERT INTO track_verdicts(track_id,status) VALUES(?,?)
               ON CONFLICT(track_id) DO UPDATE SET
                 status=excluded.status,updated_at=CURRENT_TIMESTAMP""",
            (track_id, status),
        )
        liked_id = db.execute(
            "SELECT id FROM playlists WHERE kind='liked'"
        ).fetchone()[0]
        if status == "add":
            db.execute("DELETE FROM dislikes WHERE track_id=?", (track_id,))
            db.execute(
                """INSERT OR IGNORE INTO playlist_tracks(playlist_id,track_id,position)
                   VALUES(?,?,(SELECT COALESCE(MAX(position),0)+1
                               FROM playlist_tracks WHERE playlist_id=?))""",
                (liked_id, track_id, liked_id),
            )
        else:
            db.execute(
                "DELETE FROM playlist_tracks WHERE playlist_id=? AND track_id=?",
                (liked_id, track_id),
            )
            if status == "never":
                db.execute(
                    "INSERT OR IGNORE INTO dislikes(track_id) VALUES(?)",
                    (track_id,),
                )
            else:
                db.execute("DELETE FROM dislikes WHERE track_id=?", (track_id,))
        db.execute(
            """INSERT INTO catalog_verdicts(mbid,status)
               SELECT mbid,? FROM track_recording_links WHERE track_id=?
               ON CONFLICT(mbid) DO UPDATE SET
                 status=excluded.status,updated_at=CURRENT_TIMESTAMP""",
            (status, track_id),
        )

    @staticmethod
    def _remove_like(db, track_id: int) -> bool:
        liked_id = db.execute(
            "SELECT id FROM playlists WHERE kind='liked'"
        ).fetchone()[0]
        removed = bool(db.execute(
            "DELETE FROM playlist_tracks WHERE playlist_id=? AND track_id=?",
            (liked_id, track_id),
        ).rowcount)
        db.execute(
            "DELETE FROM track_verdicts WHERE track_id=? AND status='add'",
            (track_id,),
        )
        db.execute(
            """DELETE FROM catalog_verdicts
               WHERE status='add' AND mbid IN
                  (SELECT mbid FROM track_recording_links WHERE track_id=?)""",
            (track_id,),
        )
        return removed

    @classmethod
    def _reconcile_recording_verdict(cls, db, track_id: int, mbid: str) -> None:
        """Apply the newer explicit verdict after an audio↔recording match.

        A local verdict wins equal-second ties; an older decision must never
        silently erase a more recent reaction.
        """
        local = db.execute(
            "SELECT status,updated_at FROM track_verdicts WHERE track_id=?",
            (track_id,),
        ).fetchone()
        catalog = db.execute(
            "SELECT status,updated_at FROM catalog_verdicts WHERE mbid=?",
            (mbid,),
        ).fetchone()
        if local is not None and (
            catalog is None or local["updated_at"] >= catalog["updated_at"]
        ):
            cls._apply_verdict(db, track_id, local["status"])
        elif catalog is not None:
            cls._apply_verdict(db, track_id, catalog["status"])

    def add_to(self, playlist_id, track_id):
        with self.con() as db:
            db.execute("BEGIN IMMEDIATE")
            pl = db.execute(
                "SELECT kind FROM playlists WHERE id=?", (playlist_id,)
            ).fetchone()
            if pl is None:
                raise ValueError("Playlist not found")
            if not db.execute(
                "SELECT 1 FROM tracks WHERE id=?", (track_id,)
            ).fetchone():
                raise ValueError("Track not found")
            if pl["kind"] == "liked":
                present = db.execute(
                    "SELECT 1 FROM playlist_tracks WHERE playlist_id=? AND track_id=?",
                    (playlist_id, track_id),
                ).fetchone() is not None
                self._apply_verdict(db, track_id, "add")
                return not present
            pos = db.execute(
                "SELECT COALESCE(MAX(position),0)+1 FROM playlist_tracks WHERE playlist_id=?",
                (playlist_id,),
            ).fetchone()[0]
            return bool(db.execute(
                """INSERT OR IGNORE INTO playlist_tracks(playlist_id,track_id,position)
                   VALUES(?,?,?)""",
                (playlist_id, track_id, pos),
            ).rowcount)

    def like(self, track_id):
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
        # Do not inherit playlist_tracks()'s display limit of 1000.
        with self.con() as db:
            return {int(row[0]) for row in db.execute(
                """SELECT pt.track_id FROM playlist_tracks pt
                   JOIN playlists p ON p.id=pt.playlist_id
                   WHERE p.kind='liked'"""
            )}

    @staticmethod
    def _rewrite_positions(db, playlist_id, ordered_ids):
        for position, track_id in enumerate(ordered_ids, start=1):
            db.execute(
                "UPDATE playlist_tracks SET position=? WHERE playlist_id=? AND track_id=?",
                (position, playlist_id, track_id),
            )

    def remove_from(self, playlist_id, track_id):
        with self.con() as db:
            db.execute("BEGIN IMMEDIATE")
            pl = db.execute(
                "SELECT kind FROM playlists WHERE id=?", (playlist_id,)
            ).fetchone()
            if pl is not None and pl["kind"] == "liked":
                deleted = self._remove_like(db, track_id)
            else:
                deleted = bool(db.execute(
                    "DELETE FROM playlist_tracks WHERE playlist_id=? AND track_id=?",
                    (playlist_id, track_id),
                ).rowcount)
            if deleted:
                order = [r[0] for r in db.execute(
                    "SELECT track_id FROM playlist_tracks WHERE playlist_id=? ORDER BY position",
                    (playlist_id,),
                )]
                self._rewrite_positions(db, playlist_id, order)
            return deleted

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
        self.set_track_verdict(track_id, "never")

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

    def get_wave_preset(self) -> str:
        with self.con() as db:
            row = db.execute(
                "SELECT pref_value FROM library_preferences WHERE pref_key='wave_preset'"
            ).fetchone()
            value = str(row["pref_value"]) if row else "balanced"
            return value if value in {"balanced", "familiar", "explore"} else "balanced"

    def set_wave_preset(self, preset: str) -> str:
        if preset not in {"balanced", "familiar", "explore"}:
            raise ValueError("Unknown Wave 2.0 preset")
        with self.con() as db:
            db.execute(
                """INSERT INTO library_preferences(pref_key,pref_value)
                   VALUES('wave_preset',?)
                   ON CONFLICT(pref_key) DO UPDATE SET pref_value=excluded.pref_value""",
                (preset,),
            )
        return preset

    def weekly_digest_sent(self, week_key: str) -> bool:
        if not isinstance(week_key, str) or not 1 <= len(week_key) <= 32:
            raise ValueError("Invalid digest week")
        with self.con() as db:
            return db.execute(
                "SELECT 1 FROM weekly_digest_receipts WHERE week_key=?",
                (week_key,),
            ).fetchone() is not None

    def mark_weekly_digest_sent(self, week_key: str) -> bool:
        if not isinstance(week_key, str) or not 1 <= len(week_key) <= 32:
            raise ValueError("Invalid digest week")
        with self.con() as db:
            return bool(db.execute(
                "INSERT OR IGNORE INTO weekly_digest_receipts(week_key) VALUES(?)",
                (week_key,),
            ).rowcount)

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

    def get_queue_filters(self, queue_id: int) -> dict:
        with self.con() as db:
            if db.execute("SELECT id FROM queues WHERE id=?", (queue_id,)).fetchone() is None:
                raise ValueError("Queue not found")
            row = db.execute(
                """SELECT min_rating,liked_only,no_repeat_today
                   FROM queue_filters WHERE queue_id=?""", (queue_id,),
            ).fetchone()
            return {
                "min_rating": int(row["min_rating"]) if row else 0,
                "liked_only": bool(row["liked_only"]) if row else False,
                "no_repeat_today": bool(row["no_repeat_today"]) if row else False,
            }

    def sent_today_ids(self) -> set[int]:
        """Telegram-delivered, NOT necessarily listened-to, songs."""
        with self.con() as db:
            return {int(r[0]) for r in db.execute(
                "SELECT DISTINCT track_id FROM sent_history "
                "WHERE sent_at>=datetime('now','start of day')"
            )}

    def set_queue_filters(self, queue_id: int, *, min_rating: int,
                          liked_only: bool, no_repeat_today: bool) -> dict:
        if type(min_rating) is not int or not 0 <= min_rating <= 10:
            raise ValueError("min_rating must be 0..10")
        if type(liked_only) is not bool or type(no_repeat_today) is not bool:
            raise ValueError("Queue filters must be booleans")
        with self.con() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT source_kind FROM queues WHERE id=?", (queue_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Queue not found")
            if row["source_kind"] not in {"balanced", "discover", "favorites"}:
                raise ValueError("Filters apply to generated radio queues only")
            db.execute(
                """INSERT INTO queue_filters(queue_id,min_rating,liked_only,no_repeat_today)
                   VALUES(?,?,?,?) ON CONFLICT(queue_id) DO UPDATE SET
                     min_rating=excluded.min_rating,liked_only=excluded.liked_only,
                     no_repeat_today=excluded.no_repeat_today""",
                (queue_id, min_rating, int(liked_only), int(no_repeat_today)),
            )
        return self.get_queue_filters(queue_id)

    def queue_snapshot(self, queue_id: int, *, limit: int = 50) -> dict:
        if not 1 <= limit <= 100:
            raise ValueError("Invalid preview size")
        with self.con() as db:
            queue = db.execute(
                "SELECT id,label,source_kind,cursor FROM queues WHERE id=?",
                (queue_id,),
            ).fetchone()
            if queue is None:
                raise ValueError("Queue not found")
            pending_total = int(db.execute(
                "SELECT COUNT(*) FROM queue_items WHERE queue_id=? AND position>=?",
                (queue_id, queue["cursor"]),
            ).fetchone()[0])
            upcoming = [
                {"position": int(r["position"]), "track_id": int(r["id"]),
                 "title": r["title"], "artist": r["artist"]}
                for r in db.execute(
                    """SELECT qi.position,t.id,t.title,t.artist FROM queue_items qi
                       JOIN tracks t ON t.id=qi.track_id
                       WHERE qi.queue_id=? AND qi.position>=?
                       ORDER BY qi.position LIMIT ?""",
                    (queue_id, queue["cursor"], limit),
                )
            ]
            return {
                "queue_id": int(queue["id"]), "label": queue["label"],
                "kind": queue["source_kind"], "cursor": int(queue["cursor"]),
                "pending_total": pending_total, "upcoming": upcoming,
                "is_dynamic": queue["source_kind"] in {"balanced", "discover", "favorites"},
            }

    def edit_queue(self, queue_id: int, action: str, *,
                   track_id: int | None = None, position: int | None = None,
                   to_position: int | None = None,
                   expected_cursor: int | None = None) -> dict:
        """Atomically edit ONLY not-yet-sent queue items (0-based positions).

        New tracks may be inserted at the front or appended. Moving/removing
        requires an exact pending position, not a title or vague track ID.
        Never mutate an in-flight send. Telegram already-delivered media
        is immutable.
        """
        if action not in {"next", "append", "move", "remove"}:
            raise ValueError("Unsupported queue operation")
        with self.con() as db:
            db.execute("BEGIN IMMEDIATE")
            q = db.execute(
                "SELECT cursor FROM queues WHERE id=?", (queue_id,),
            ).fetchone()
            if q is None:
                raise ValueError("Queue not found")
            cursor = int(q["cursor"])
            if expected_cursor is not None and expected_cursor != cursor:
                raise ValueError("Queue changed; refresh the pending list")
            locked = db.execute(
                """SELECT 1 FROM media_import_leases
                   WHERE source_type='queue' AND source_key=?
                     AND expires_at>unixepoch('now')""",
                (str(queue_id),),
            ).fetchone()
            if locked:
                raise ValueError("Queue is currently sending audio")
            pending = [int(r[0]) for r in db.execute(
                "SELECT track_id FROM queue_items WHERE queue_id=? "
                "AND position>=? ORDER BY position", (queue_id, cursor),
            )]
            if action in {"next", "append"}:
                if type(track_id) is not int or track_id <= 0:
                    raise ValueError("A valid track ID is required")
                if len(pending) >= 1000:
                    raise ValueError("Queue limit is 1000 pending tracks")
                if db.execute(
                    "SELECT 1 FROM tracks WHERE id=?", (track_id,)
                ).fetchone() is None:
                    raise ValueError("Track not found")
                pending.insert(0 if action == "next" else len(pending), track_id)
            else:
                if type(position) is not int or not cursor <= position < cursor+len(pending):
                    raise ValueError("Cannot edit an already sent or missing position")
                current_idx = position - cursor
                if action == "remove":
                    pending.pop(current_idx)
                else:
                    if (type(to_position) is not int or
                            not cursor <= to_position < cursor+len(pending)):
                        raise ValueError("Target position must still be pending")
                    item = pending.pop(current_idx)
                    pending.insert(to_position - cursor, item)
            db.execute(
                "DELETE FROM queue_items WHERE queue_id=? AND position>=?",
                (queue_id, cursor),
            )
            db.executemany(
                "INSERT INTO queue_items(queue_id,position,track_id) VALUES(?,?,?)",
                [(queue_id, cursor+i, tid) for i, tid in enumerate(pending)],
            )
        return self.queue_snapshot(queue_id)

    def recommendation_signals(self, seed_ids, *, history_limit: int = 240) -> dict:
        """Passive *exposure*, not inferred listens or dislikes.

        Summarize recently delivered tracks and explicit custom-playlist
        membership. No network calls, new schema, or user interaction.
        A playlist with a favorite acts as a weak cross-artist similarity hint.
        """
        if not 1 <= history_limit <= 2000:
            raise ValueError("Invalid history window")
        with self.con() as db:
            sent = db.execute(
                "SELECT track_id FROM sent_history ORDER BY id DESC LIMIT ?",
                (history_limit,),
            ).fetchall()
            pairs = db.execute(
                """SELECT pt.playlist_id, pt.track_id
                   FROM playlist_tracks pt
                   JOIN playlists p ON p.id=pt.playlist_id
                   WHERE p.kind='custom'"""
            ).fetchall()
        exposure: dict[int, float] = {}
        for position, row in enumerate(sent):
            track_id = int(row["track_id"])
            # Decayed *delivery frequency* gives the latest cards more weight.
            # It never implies that Telegram actually played the audio.
            exposure[track_id] = exposure.get(track_id, 0.0) + 1.0 / (1.0 + position / 25.0)
        playlists: dict[int, set[int]] = {}
        counts: dict[int, int] = {}
        for row in pairs:
            playlist_id, track_id = int(row["playlist_id"]), int(row["track_id"])
            playlists.setdefault(playlist_id, set()).add(track_id)
            counts[track_id] = counts.get(track_id, 0) + 1
        seed = set(seed_ids)
        related: set[int] = set()
        for members in playlists.values():
            # Giant catch-all playlists are not reliable similarity evidence.
            if 2 <= len(members) <= 80 and members & seed:
                related.update(members - seed)
        return {
            "exposure": exposure,
            "playlist_counts": counts,
            "related_ids": related,
        }

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

    def prune_automatic_pending(self, queue_id: int) -> int:
        """Drop parked tracks still pending in a generated radio queue.

        Call only while owning the queue-delivery lease, never for manually
        selected playlists or reaction review lists.
        """
        with self.con() as db:
            db.execute("BEGIN IMMEDIATE")
            queue = db.execute(
                "SELECT cursor,source_kind FROM queues WHERE id=?", (queue_id,)
            ).fetchone()
            if queue is None:
                raise ValueError("Queue not found")
            if queue["source_kind"] not in {"balanced", "discover", "favorites"}:
                return 0
            cursor = int(queue["cursor"])
            current = [int(row[0]) for row in db.execute(
                """SELECT track_id FROM queue_items WHERE queue_id=?
                   AND position>=? ORDER BY position""",
                (queue_id, cursor),
            )]
            suppressed = {
                int(row[0]) for row in db.execute(
                    """SELECT track_id FROM track_verdicts
                       WHERE status IN ('never','later','interesting')"""
                )
            }
            pending = [tid for tid in current if tid not in suppressed]
            if len(pending) == len(current):
                return 0
            db.execute(
                "DELETE FROM queue_items WHERE queue_id=? AND position>=?",
                (queue_id, cursor),
            )
            db.executemany(
                "INSERT INTO queue_items(queue_id,position,track_id) VALUES(?,?,?)",
                [(queue_id, cursor+i, tid) for i,tid in enumerate(pending)],
            )
            return len(current) - len(pending)

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


    # Five exclusive discovery decisions. They coexist with optional 1-10
    # scores. Unlike an explicit skip, "later" is not negative feedback.
    VERDICT_STATUSES = frozenset(("maybe", "add", "never", "later", "interesting"))

    def set_track_verdict(self, track_id: int, status: str) -> None:
        """Keep five decisions exclusive across all local/linked tables."""
        if status not in self.VERDICT_STATUSES or type(track_id) is not int or track_id < 1:
            raise ValueError("Invalid music decision")
        with self.con() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM tracks WHERE id=?", (track_id,)).fetchone() is None:
                raise ValueError("Track not found")
            self._apply_verdict(db, track_id, status)

    def set_catalog_verdict(self, mbid: str, status: str) -> None:
        """Catalog-only ratings remain decisions, not imaginary Telegram audio."""
        if status not in self.VERDICT_STATUSES or not isinstance(mbid, str):
            raise ValueError("Invalid music decision")
        with self.con() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM catalog_candidates WHERE mbid=?", (mbid,)
            ).fetchone() is None:
                raise ValueError("Recording not found in catalog")
            db.execute(
                """INSERT INTO catalog_verdicts(mbid,status) VALUES(?,?)
                   ON CONFLICT(mbid) DO UPDATE SET
                    status=excluded.status,updated_at=CURRENT_TIMESTAMP""",
                (mbid, status),
            )
            record = db.execute(
                "SELECT track_id FROM track_recording_links WHERE mbid=? LIMIT 1",
                (mbid,),
            ).fetchone()
            if record is None:
                record = db.execute(
                    "SELECT track_id FROM catalog_audio_assets WHERE mbid=?",
                    (mbid,),
                ).fetchone()
            if record is not None:
                self._apply_verdict(db, int(record["track_id"]), status)

    def track_verdicts(self) -> dict[int, str]:
        with self.con() as db:
            return {
                int(r["track_id"]): str(r["status"])
                for r in db.execute("SELECT track_id,status FROM track_verdicts")
            }

    def catalog_verdicts(self) -> dict[str, str]:
        with self.con() as db:
            return {
                str(r["mbid"]): str(r["status"])
                for r in db.execute("SELECT mbid,status FROM catalog_verdicts")
            }

    def suppressed_verdict_track_ids(self) -> set[int]:
        """Park 'later' and 'interesting'; never actively recommend either."""
        with self.con() as db:
            return {
                int(r["track_id"]) for r in db.execute(
                    "SELECT track_id FROM track_verdicts WHERE status IN ('later','interesting','never')"
                )
            }

    _VERDICT_ITEM_QUERY = """
        SELECT source,track_id,mbid,title,artist,updated_at FROM (
            SELECT 'local' AS source,t.id AS track_id,l.mbid,t.title,t.artist,
                   v.updated_at
            FROM track_verdicts v
            JOIN tracks t ON t.id=v.track_id
            LEFT JOIN track_recording_links l ON l.track_id=t.id
            WHERE v.status=?
            UNION ALL
            SELECT 'catalog' AS source,
                   COALESCE(l.track_id,a.track_id) AS track_id,
                   v.mbid,c.title,c.artist,v.updated_at
            FROM catalog_verdicts v
            JOIN catalog_candidates c ON c.mbid=v.mbid
            LEFT JOIN track_recording_links l ON l.mbid=v.mbid
            LEFT JOIN catalog_audio_assets a ON a.mbid=v.mbid
            WHERE v.status=? AND NOT EXISTS(
              SELECT 1 FROM track_verdicts tv
              WHERE tv.track_id=COALESCE(l.track_id,a.track_id))
        )
    """

    def verdict_items(
        self, status: str, limit: int = 10000, offset: int = 0,
    ) -> list[dict]:
        """SQL-paginated mixed library + metadata-only playlist."""
        if (status not in self.VERDICT_STATUSES
                or type(limit) is not int or not 1 <= limit <= 10000
                or type(offset) is not int or offset < 0):
            raise ValueError("Invalid verdict list")
        with self.con() as db:
            return [dict(row) for row in db.execute(
                self._VERDICT_ITEM_QUERY +
                " ORDER BY updated_at DESC,source DESC,mbid LIMIT ? OFFSET ?",
                (status, status, limit, offset),
            )]

    def verdict_counts(self) -> dict[str, int]:
        """Actual unbounded counts, independent of any page size."""
        query = (
            "SELECT COUNT(*) FROM (" + self._VERDICT_ITEM_QUERY + ")"
        )
        with self.con() as db:
            return {
                status: int(db.execute(query, (status,status)).fetchone()[0])
                for status in self.VERDICT_STATUSES
            }

    def verdict_playable_ids(self, status: str, limit: int = 10000) -> list[int]:
        if status not in self.VERDICT_STATUSES or not 1 <= limit <= 10000:
            raise ValueError("Invalid verdict playlist")
        query = (
            "SELECT DISTINCT track_id FROM (" + self._VERDICT_ITEM_QUERY +
            ") WHERE track_id IS NOT NULL ORDER BY updated_at DESC LIMIT ?"
        )
        with self.con() as db:
            return [int(row[0]) for row in db.execute(
                query, (status, status, limit),
            )]

    def catalog_preference_artists(self) -> list[tuple[str, str]]:
        """Positive external decisions not already represented by a local file."""
        with self.con() as db:
            return [(row["artist"], row["status"]) for row in db.execute(
                """SELECT c.artist,v.status FROM catalog_verdicts v
                   JOIN catalog_candidates c ON c.mbid=v.mbid
                   LEFT JOIN track_recording_links l ON l.mbid=v.mbid
                   LEFT JOIN catalog_audio_assets a ON a.mbid=v.mbid
                   WHERE v.status IN ('add','maybe')
                     AND COALESCE(l.track_id,a.track_id) IS NULL"""
            )]

    def sync_taste_source(
        self, provider: str, songs, *, public_url: str | None = None,
        complete: bool = True,
    ) -> dict:
        """Commit a validated snapshot atomically. Partial TXT never deletes."""
        from music_bot.catalog import normalize
        if provider not in {"yandex", "youtube_music"}:
            raise ValueError("Unsupported music source")
        if not songs or len(songs) > 10000:
            raise ValueError("Music import cannot be empty or exceed 10000 items")
        if public_url is not None and (
            provider != "yandex" or len(public_url) > 350 or
            not public_url.startswith("https://music.yandex.")
        ):
            raise ValueError("Invalid Yandex source URL")
        by_id = {}
        for song in songs:
            ident = str(song.external_id)
            title, artist = song.title.strip(), song.artist.strip()
            if not ident or not title or not artist:
                continue
            by_id[ident] = (ident, title, artist, song.url, song.liked_at,
                            normalize(title), normalize(artist))
        if not by_id:
            raise ValueError("No valid liked songs were supplied")
        with self.con() as db:
            db.execute("BEGIN IMMEDIATE")
            prev = {
                str(r[0]) for r in db.execute(
                    "SELECT external_id FROM taste_tracks WHERE provider=? AND active=1",
                    (provider,),
                )
            }
            db.execute(
                """INSERT INTO taste_sources(provider,public_url,is_complete)
                   VALUES(?,?,?) ON CONFLICT(provider) DO UPDATE SET
                     public_url=COALESCE(excluded.public_url,taste_sources.public_url),
                     is_complete=excluded.is_complete,
                     last_synced_at=CURRENT_TIMESTAMP""",
                (provider, public_url, int(complete)),
            )
            if complete:
                db.execute(
                    "UPDATE taste_tracks SET active=0 WHERE provider=?", (provider,)
                )
            for item in by_id.values():
                db.execute(
                    """INSERT INTO taste_tracks(
                         provider,external_id,title,artist,source_url,liked_at,
                         normalized_title,normalized_artist,active)
                       VALUES(?,?,?,?,?,?,?,?,1)
                       ON CONFLICT(provider,external_id) DO UPDATE SET
                         title=excluded.title,artist=excluded.artist,
                         source_url=excluded.source_url,
                         liked_at=COALESCE(excluded.liked_at,taste_tracks.liked_at),
                         normalized_title=excluded.normalized_title,
                         normalized_artist=excluded.normalized_artist,
                         active=1,last_seen_at=CURRENT_TIMESTAMP""",
                    (provider,*item),
                )
            now_ids = {
                str(r[0]) for r in db.execute(
                    "SELECT external_id FROM taste_tracks WHERE provider=? AND active=1",
                    (provider,),
                )
            }
        return {
            "provider": provider, "active": len(now_ids),
            "added": len(now_ids - prev),
            "removed": len(prev - now_ids) if complete else 0,
            "complete": bool(complete),
        }

    def taste_sources(self) -> list[dict]:
        with self.con() as db:
            return [dict(r) for r in db.execute(
                """SELECT s.provider,s.public_url,s.is_complete,s.last_synced_at,
                          COUNT(t.external_id) AS count
                   FROM taste_sources s LEFT JOIN taste_tracks t
                     ON s.provider=t.provider AND t.active=1
                   GROUP BY s.provider ORDER BY s.provider"""
            )]

    def taste_tracks(self, provider: str, *, limit: int = 30,
                     offset: int = 0) -> list[dict]:
        if provider not in {"yandex", "youtube_music"} or not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid source query")
        with self.con() as db:
            return [dict(r) for r in db.execute(
                """SELECT external_id,title,artist,source_url,liked_at
                   FROM taste_tracks WHERE provider=? AND active=1
                   ORDER BY COALESCE(liked_at,last_seen_at) DESC,external_id
                   LIMIT ? OFFSET ?""",
                (provider,limit,offset),
            )]

    def taste_artist_scores(self) -> list[tuple[str, float]]:
        """External favorites are positive *likes*, never invented plays."""
        with self.con() as db:
            rows = db.execute(
                """SELECT artist,normalized_title,normalized_artist,liked_at
                   FROM taste_tracks WHERE active=1
                   ORDER BY liked_at DESC"""
            ).fetchall()
        seen = set()
        weights = {}
        names = {}
        for row in rows:
            key = (row["normalized_title"],row["normalized_artist"])
            if key in seen or not key[0] or not key[1]:
                continue
            seen.add(key)
            artist = row["normalized_artist"]
            names.setdefault(artist,row["artist"])
            # Cap individual artists to prevent bulk imports drowning explicit
            # ratings. Recency only matters when the source supplied a date.
            weights[artist] = min(4.0, weights.get(artist,0.0) + 0.30)
        return [(names[k],v) for k,v in sorted(
            weights.items(),key=lambda pair:(-pair[1],pair[0]))]

    def taste_identities(self) -> set[tuple[str, str]]:
        with self.con() as db:
            return {(str(r[0]),str(r[1])) for r in db.execute(
                """SELECT normalized_title,normalized_artist FROM taste_tracks
                   WHERE active=1"""
            )}

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
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM catalog_candidates WHERE mbid=?", (mbid,)).fetchone():
                raise ValueError("Unknown catalog recording")
            if not db.execute("SELECT 1 FROM tracks WHERE id=?", (track_id,)).fetchone():
                raise ValueError("Unknown Telegram audio track")
            other_recording = db.execute(
                "SELECT mbid FROM track_recording_links WHERE track_id=?",
                (track_id,),
            ).fetchone()
            if (other_recording is not None and
                    other_recording["mbid"] != mbid):
                raise ValueError("Telegram audio already linked to another recording")
            other_audio = db.execute(
                "SELECT track_id FROM catalog_audio_assets WHERE mbid=?",
                (mbid,),
            ).fetchone()
            if other_audio is not None and int(other_audio["track_id"]) != track_id:
                raise ValueError("Recording already linked to different Telegram audio")
            # A manually confirmed recording is also a real playable file.
            # Never overwrite its identity with a second upload.
            confirmed_elsewhere = db.execute(
                """SELECT track_id FROM track_recording_links
                   WHERE mbid=? AND track_id<>? LIMIT 1""",
                (mbid, track_id),
            ).fetchone()
            if confirmed_elsewhere is not None:
                raise ValueError("Recording already associated with another track")
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
            self._reconcile_recording_verdict(db, track_id, mbid)

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
            db.execute("BEGIN IMMEDIATE")
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
            another = db.execute(
                """SELECT track_id FROM track_recording_links
                   WHERE mbid=? AND track_id<>? LIMIT 1""",
                (mbid, track_id),
            ).fetchone()
            if another is not None:
                raise ValueError("Recording already belongs to another Telegram track")
            db.execute(
                """INSERT INTO track_recording_links(track_id,mbid,provenance)
                   VALUES(?,?,'confirmed')
                   ON CONFLICT(track_id) DO UPDATE SET
                    mbid=excluded.mbid, provenance='confirmed',
                    linked_at=CURRENT_TIMESTAMP""",
                (track_id, mbid),
            )
            self._reconcile_recording_verdict(db, track_id, mbid)
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
            other_identity = db.execute(
                "SELECT track_id FROM track_recording_links WHERE mbid=? AND track_id<>? LIMIT 1",
                (mbid, track_id),
            ).fetchone()
            if other_identity is not None:
                raise ValueError("This recording is already confirmed for another audio")
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
            self._reconcile_recording_verdict(db, track_id, mbid)
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
        if kind not in {"youtube", "musicbrainz", "global", "queue", "weekly"}:
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
