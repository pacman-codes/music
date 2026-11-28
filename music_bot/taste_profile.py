"""Inspectable current Wave taste, not reconstructed historical listening."""
from __future__ import annotations

import asyncio
from collections import Counter

from music_bot.catalog import CatalogError, valid_mbid
from music_bot.recommend import artist_key, fatigue_suppressed
from music_bot.selection import wave_preferences
from music_bot.wave_v2 import classify


def current_context(db):
    tracks = db.all_tracks(10000)
    liked, disliked = db.liked_ids(), db.disliked_ids()
    ratings, playback = db.ratings(), db.playback_stats()
    suppressed, verdicts = db.suppressed_verdict_track_ids(), db.track_verdicts()
    prefs = wave_preferences(db, tracks, liked_ids=liked, disliked_ids=disliked,
                             ratings=ratings, playback=playback,
                             suppressed=suppressed, verdicts=verdicts)
    eligible = [t for t in tracks if t.id not in suppressed and t.id not in disliked
                and not fatigue_suppressed(*playback.get(t.id, (0, 0)))]
    return tracks, eligible, suppressed, verdicts, prefs


def music_profile(db):
    tracks, eligible, _, verdicts, prefs = current_context(db)
    names = {artist_key(t.artist): t.artist.strip() for t in tracks}
    favorites = db.saved_artists(limit=100)
    for row in favorites:
        names[artist_key(row["name"])] = row["name"]
    for name, _ in db.taste_artist_scores() + db.catalog_preference_artists():
        names.setdefault(artist_key(name), name)
    recent = Counter(artist_key(t.artist) for t in eligible
                     if t.id in prefs["fresh_positive_ids"] and
                     prefs["ratings"].get(t.id, 10) >= 5)
    groups = classify(eligible, prefs["liked_ids"], prefs["ratings"], prefs["playback"],
                      prefs["related_ids"], preferred_artists=prefs["preferred_artists"])
    affinity = prefs["artist_affinity"]
    artists = [{"name": names.get(key,key), "affinity": round(score,2),
                "recent_positive_tracks": recent[key],
                "preferred": key in prefs["preferred_artists"]}
               for key,score in sorted(affinity.items(),key=lambda x:(-x[1],x[0]))[:12]]
    return {
        "artists": artists,
        "recent_positive_artists": [{"name": names.get(key,key), "tracks": n}
                                    for key,n in recent.most_common(10)],
        "recent_window_days": 30,
        "library_count": len(tracks),
        "eligible_count": len(eligible),
        "liked_count": len(prefs["liked_ids"]),
        "rated_count": len(prefs["ratings"]),
        "reaction_counts": dict(Counter(verdicts.values())),
        "categories": {key: len(rows) for key,rows in groups.items()},
        "settings": {**db.get_wave_settings(), "preset": db.get_wave_preset()},
        "scope": "current_preferences",
        "library_sample_limit": 10000,
        "historical_trend_available": False,
    }


def explain_wave_track(db, track_id: int):
    track = db.get_track(track_id)
    if track is None:
        raise ValueError("Трек не найден")
    _, eligible, suppressed, verdicts, prefs = current_context(db)
    rating = prefs["ratings"].get(track_id)
    skips, completes = prefs["playback"].get(track_id, (0,0))
    key = artist_key(track.artist)
    reasons, cautions = [], []
    if track_id in prefs["liked_ids"]:
        reasons.append("В избранном")
    if rating is not None:
        (reasons if rating >= 8 else cautions if rating < 5 else reasons).append(
            f"Твоя оценка: {rating}/10")
    if track_id in prefs["maybe_ids"]:
        reasons.append("Реакция «Может добавлю»")
    if key in prefs["preferred_artists"] and (rating is None or rating >= 5):
        reasons.append("Исполнитель входит в текущий профиль вкуса")
    if track_id in prefs["related_ids"]:
        reasons.append("Есть в небольшом плейлисте вместе с любимыми песнями")
    if track_id in prefs["fresh_positive_ids"] and (rating is None or rating >= 5):
        reasons.append("Недавняя положительная оценка или реакция")
    if completes:
        reasons.append(f"Ручные отметки дослушивания: {completes}")
    if skips:
        cautions.append(f"Ручные отметки пропуска: {skips}")
    if track_id in prefs["rotation_ids"] or track_id in prefs["immediate_ids"]:
        cautions.append("Недавно отправлялся в Telegram: приоритет повтора снижен")
    elif track_id in prefs["recent_ids"]:
        cautions.append("Есть в недавней истории отправок")
    if track_id in suppressed or track_id in prefs["disliked_ids"]:
        cautions.append("Исключён из автоматических рекомендаций по реакции")
    if fatigue_suppressed(skips, completes):
        cautions.append("Исключён из Волны из-за частых ручных пропусков")
    groups = classify(eligible, prefs["liked_ids"], prefs["ratings"], prefs["playback"],
                      prefs["related_ids"], preferred_artists=prefs["preferred_artists"])
    category = next((name for name,rows in groups.items()
                     if any(t.id==track_id for t in rows)), None)
    if category=="new" and not reasons:
        reasons.append("Исследование других песен из медиатеки")
    return {"track_id": track_id, "title": track.title, "artist": track.artist,
            "eligible": category is not None, "category": category,
            "reasons": reasons, "cautions": cautions,
            "scope": "current_factors_not_historical_selection",
            "note": "Текущие факторы Волны, а не сохранённая причина прошлой рекомендации. Выбор зависит от режима, очереди, ротации и случайной выборки."}


async def profile_genres(db, catalog):
    """Only explicit saved artist MBIDs; tags are artist metadata, not audio analysis."""
    lease = db.acquire_media_import("taste", "profile-genres", ttl=60)
    if lease is None:
        raise CatalogError("Жанры уже загружаются")
    try:
        results, missing = [], 0
        artists = db.saved_artists(limit=3)
        for artist in artists:
            ident = artist["mbid"]
            if not valid_mbid(ident):
                continue
            try:
                data = await asyncio.wait_for(catalog._json(
                    "mb", "/ws/2/artist/"+ident, {"fmt":"json","inc":"genres"},
                    ttl=7*86400), timeout=12)
            except (CatalogError, TimeoutError):
                missing += 1
                continue
            if not isinstance(data,dict) or data.get("id") != ident:
                missing += 1
                continue
            genres = data.get("genres")
            if not isinstance(genres,list):
                missing += 1
                continue
            labels = []
            for item in genres[:30]:
                if isinstance(item,dict) and isinstance(item.get("name"),str):
                    name = item["name"].strip()[:80]
                    if name and name not in labels:
                        labels.append(name)
            results.append({"name":artist["name"],"mbid":ident,"genres":labels[:8]})
        return {"artists":results,"requested":len(artists),"unavailable":missing,
                "source":"MusicBrainz","scope":"saved_artist_genres"}
    finally:
        db.release_media_import("taste", "profile-genres", lease)
