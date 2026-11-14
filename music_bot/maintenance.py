"""Safe opt-in maintenance of transient music SQLite records.

No deletion of library tracks, Telegram file_id, likes, ratings, playlists,
movie/anime watchlists or MusicBrainz identity mappings. Back up first.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from music_bot.storage import Library


def prune_temporary_data(database: str | Path, *, dry_run: bool = True) -> dict:
    """Return affected-row counts. Default is report-only.

    Old listening queue cards will no longer resume after 90 days.
    Radio history still keeps the last 120 days and feedback 60 days,
    both longer than the ranking windows.
    """
    db = Library(database)
    plans = {
        "expired_catalog_cache": (
            "external_catalog_cache", "expires_at <= unixepoch('now')"
        ),
        "expired_widget_bindings": (
            "widget_bindings", "expires_at <= unixepoch('now')"
        ),
        "old_widget_receipts": (
            "widget_receipts", "created_at < datetime('now','-30 days')"
        ),
        "old_queues": (
            "queues", "created_at < datetime('now','-90 days')"
        ),
        "old_sent_history": (
            "sent_history", "sent_at < datetime('now','-120 days')"
        ),
        "old_playback_events": (
            "playback_events", "created_at < datetime('now','-60 days')"
        ),
    }
    results = {}
    with db.con() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for label, (table, condition) in plans.items():
            # All table names and conditions are fixed, not user input.
            count = connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {condition}"
            ).fetchone()[0]
            results[label] = int(count)
            if not dry_run:
                connection.execute(f"DELETE FROM {table} WHERE {condition}")
        if dry_run:
            connection.rollback()
            # DB connection context will commit an empty transaction.
        else:
            connection.execute("PRAGMA optimize")
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Prune expired cache/queues; defaults to safe dry-run"
    )
    parser.add_argument("--database", default="data/music.db")
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually delete expired transient records; back up first",
    )
    arguments = parser.parse_args()
    path = Path(arguments.database)
    if not path.exists():
        parser.error(f"Database not found: {path}")
    counts = prune_temporary_data(path, dry_run=not arguments.apply)
    for label, total in counts.items():
        print(f"{label}: {total}")
    print("Changes applied." if arguments.apply else
          "Dry run only. Pass --apply after making a verified backup.")


if __name__ == "__main__":
    main()
