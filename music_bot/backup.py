"""Atomic, SQLite-consistent local backup of music metadata.

Copy the resulting file OFF the media server. Telegram file_id media is not backed up.
"""
from __future__ import annotations

import argparse
import os
import secrets
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def backup_db(database: str | Path, directory: str | Path) -> Path:
    source_path = Path(database).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"Database does not exist: {source_path}")
    target_dir = Path(directory).resolve()
    target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_name = tempfile.mkstemp(
        prefix=".music-backup-", suffix=".sqlite3.tmp", dir=target_dir
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(temp_path) as dest:
                source.backup(dest)
                check = dest.execute("PRAGMA integrity_check").fetchone()
                if check is None or check[0] != "ok":
                    raise RuntimeError("Backup integrity_check failed")
        # Ensure the generated file is written durably before atomic publication.
        with temp_path.open("rb") as handle:
            os.fsync(handle.fileno())
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        final = target_dir / f"music-{stamp}-{secrets.token_hex(4)}.sqlite3"
        temp_path.replace(final)
        return final
    finally:
        temp_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Backup music metadata SQLite")
    parser.add_argument("--database", default="data/music.db")
    parser.add_argument("--output", default="backups")
    args = parser.parse_args()
    destination = backup_db(args.database, args.output)
    print(f"Validated local SQLite backup: {destination}")
    print("Copy it to storage outside the server; Telegram media files are not included.")


if __name__ == "__main__":
    main()
