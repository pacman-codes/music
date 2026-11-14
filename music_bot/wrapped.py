"""Music Wrapped built only from verifiable private SQLite events.

Native Telegram sendAudio is an *outgoing delivery*, not a confirmed listen.
Only user-reported skips/completions are called manual playback feedback.
Never infer listening minutes, genres or actual play counts from Bot API.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from music_bot.storage import Library

PERIODS = {"7d": 7, "30d": 30, "365d": 365, "all": None}


def wrapped_summary(db: Library, period: str = "30d", *,
                    now: datetime | None = None) -> dict:
    if period not in PERIODS:
        raise ValueError("Unknown statistics period")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("Expected offset-aware current date")
    current = current.astimezone(timezone.utc)
    days = PERIODS[period]
    since = (current - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S") if days else None

    def condition(column: str):
        return (f" WHERE {column}>=?", (since,)) if since else ("", ())

    with db.con() as sql:
        sent_where, sent_args = condition("h.sent_at")
        total_deliveries = int(sql.execute(
            "SELECT COUNT(*) FROM sent_history h" + sent_where, sent_args
        ).fetchone()[0])
        unique_sent = int(sql.execute(
            "SELECT COUNT(DISTINCT h.track_id) FROM sent_history h"
            + sent_where, sent_args,
        ).fetchone()[0])
        artist_rows = sql.execute(
            """SELECT t.artist, COUNT(*) AS delivered,
                      COUNT(DISTINCT t.id) AS distinct_tracks
               FROM sent_history h JOIN tracks t ON t.id=h.track_id"""
            + sent_where +
            " GROUP BY t.artist ORDER BY delivered DESC,t.artist COLLATE NOCASE LIMIT 8",
            sent_args,
        ).fetchall()
        top_tracks = [
            {"track_id":int(r["id"]), "title":r["title"], "artist":r["artist"],
             "deliveries":int(r["delivered"])}
            for r in sql.execute(
                """SELECT t.id,t.title,t.artist,COUNT(*) AS delivered
                   FROM sent_history h JOIN tracks t ON t.id=h.track_id"""
                + sent_where +
                " GROUP BY t.id ORDER BY delivered DESC,t.id ASC LIMIT 8",
                sent_args,
            )
        ]
        event_where, event_args = condition("created_at")
        manual = sql.execute(
            """SELECT event, COUNT(*) AS total FROM playback_events
               WHERE source='manual'"""
            + (" AND created_at>=?" if since else "") +
            " GROUP BY event",
            (since,) if since else (),
        ).fetchall()
        event_totals = {str(r["event"]):int(r["total"]) for r in manual}
        current_likes = int(sql.execute(
            """SELECT COUNT(*) FROM playlist_tracks p JOIN playlists s
               ON s.id=p.playlist_id WHERE s.kind='liked'"""
        ).fetchone()[0])
        rated = sql.execute(
            "SELECT COUNT(*) AS rated,AVG(score) AS mean FROM track_ratings"
        ).fetchone()
        candidates = sql.execute(
            "SELECT COUNT(*) FROM discovery_wishlist"
            + (" WHERE created_at>=?" if since else ""),
            (since,) if since else (),
        ).fetchone()[0]
        imported = sql.execute(
            "SELECT COUNT(*) FROM catalog_audio_assets"
            + (" WHERE linked_at>=?" if since else ""),
            (since,) if since else (),
        ).fetchone()[0]
        library_size = int(sql.execute(
            "SELECT COUNT(*) FROM tracks"
        ).fetchone()[0])
        top_favorites = [
            {"artist":r["artist"],"liked_tracks":int(r["liked_tracks"]),
             "average_rating":round(float(r["avg_rating"]),1)}
            for r in sql.execute(
                """SELECT t.artist,COUNT(*) AS liked_tracks,
                          AVG(COALESCE(r.score,5)) AS avg_rating
                   FROM playlist_tracks p JOIN playlists pl
                   ON pl.id=p.playlist_id AND pl.kind='liked'
                   JOIN tracks t ON t.id=p.track_id
                   LEFT JOIN track_ratings r ON r.track_id=t.id
                   GROUP BY t.artist
                   ORDER BY liked_tracks DESC,avg_rating DESC,t.artist COLLATE NOCASE
                   LIMIT 8"""
            )
        ]
        highly_rated = [
            {"track_id":int(r["id"]), "title":r["title"], "artist":r["artist"],
             "score":int(r["score"])}
            for r in sql.execute(
                """SELECT t.id,t.title,t.artist,r.score
                   FROM track_ratings r JOIN tracks t ON t.id=r.track_id
                   ORDER BY r.score DESC,r.updated_at DESC,t.id LIMIT 8"""
            )
        ]
        day_counts = {
            str(row["day"]):int(row["sent"])
            for row in sql.execute(
                """SELECT date(sent_at) AS day,COUNT(*) AS sent
                   FROM sent_history WHERE sent_at>=?
                   GROUP BY date(sent_at)""",
                ((current - timedelta(days=7)).strftime("%Y-%m-%d"),),
            )
        }
    histogram = [
        {"date":(current.date()-timedelta(days=i)).isoformat(),
         "deliveries":day_counts.get((current.date()-timedelta(days=i)).isoformat(),0)}
        for i in reversed(range(7))
    ]
    return {
        "period":period,"period_days":days,"as_of":current.isoformat(),
        "statistics_basis":"telegram_deliveries_and_explicit_manual_feedback",
        "deliveries":total_deliveries,"distinct_sent_tracks":unique_sent,
        "library_tracks":library_size,"favorites_count":current_likes,
        "rated_count":int(rated["rated"]),
        "average_rating":(round(float(rated["mean"]),2)
                          if rated["mean"] is not None else None),
        "manual_skips":event_totals.get("skip",0),
        "manual_completions":event_totals.get("complete",0),
        "saved_discoveries":int(candidates),
        "imported_catalog_audio":int(imported),
        "top_artists":[{"artist":r["artist"],"deliveries":int(r["delivered"]),
                        "distinct_tracks":int(r["distinct_tracks"])}
                       for r in artist_rows],
        "top_tracks":top_tracks,
        "favorite_artists":top_favorites,
        "highly_rated":highly_rated,
        "daily_deliveries":histogram,
    }
