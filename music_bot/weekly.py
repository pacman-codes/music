"""Weekly personal music discoveries + fresh releases (metadata only).

ListenBrainz general fresh-release feed requires no personal LB username.
We prioritize artists supported by the owner's explicit favorites/ratings,
while leaving room for unfamiliar releases. Not a proof of playable audio.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any

from music_bot.catalog import CatalogError, MusicCatalog, normalize, valid_mbid
from music_bot.personalized import personalized_discovery, ranked_seed_artists
from music_bot.storage import Library


@dataclass(frozen=True)
class FreshRelease:
    mbid: str
    name: str
    artist: str
    release_date: str
    release_type: str
    favorite_artist: bool

    def public(self) -> dict:
        return {**asdict(self), "playable": False, "type": "release_metadata"}


def parse_fresh_releases(data: Any, artist_seeds: list[str], *,
                         today: date | None = None,
                         limit: int = 12) -> list[FreshRelease]:
    if not 1 <= limit <= 30:
        raise ValueError("Invalid weekly release count")
    payload = data.get("payload") if isinstance(data, dict) else None
    rows = payload.get("releases") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise CatalogError("ListenBrainz вернул некорректный список релизов")
    current = today or datetime.now(timezone.utc).date()
    favored = {normalize(s) for s in artist_seeds}
    best: dict[str, FreshRelease] = {}
    for entry in rows[:200]:
        if not isinstance(entry, dict):
            continue
        name, artist, mid, released = (
            entry.get("release_name"), entry.get("artist_credit_name"),
            entry.get("release_group_mbid") or entry.get("release_mbid"),
            entry.get("release_date"),
        )
        if (not valid_mbid(mid) or
                not isinstance(name,str) or not name.strip() or
                not isinstance(artist,str) or not artist.strip() or
                not isinstance(released,str)):
            continue
        try:
            when=date.fromisoformat(released)
        except ValueError:
            continue
        days_ago=(current-when).days
        if not 0 <= days_ago <= 13:  # recent two weeks, no future guesses
            continue
        mid=mid.lower()
        item=FreshRelease(
            mid,name.strip()[:180],artist.strip()[:140],released,
            str(entry.get("release_group_primary_type") or "Release")[:36],
            normalize(artist) in favored,
        )
        best[mid]=item
    # Seed artists first; then a deliberate, smaller exploratory shelf.
    ordered=sorted(
        best.values(),
        key=lambda r:(not r.favorite_artist, -date.fromisoformat(r.release_date).toordinal(),
                      r.artist.casefold(),r.mbid),
    )
    if not favored:
        return ordered[:limit]
    close=[r for r in ordered if r.favorite_artist][:max(1,limit*3//4)]
    other=[r for r in ordered if not r.favorite_artist]
    result=close+other[:max(0,limit-len(close))]
    return result[:limit]


async def weekly_discovery(db: Library, catalog: MusicCatalog, *,
                           limit: int = 10,
                           today: date | None = None) -> dict:
    if not 1 <= limit <= 20:
        raise ValueError("Weekly limit must be 1..20")
    current=today or datetime.now(timezone.utc).date()
    seeds=[seed.name for seed in ranked_seed_artists(db,count=3)]
    releases: list[FreshRelease] = []
    songs = []
    failures = []
    try:
        data=await catalog._json(
            "lb","/1/explore/fresh-releases/",
            {"days":14,"past":"true","future":"false"},
            ttl=12*3600,
        )
        releases=parse_fresh_releases(data,seeds,today=current,limit=limit)
    except CatalogError:
        failures.append("Fresh releases are temporarily unavailable")
    try:
        _, songs = await personalized_discovery(
            db,catalog,difficulty="hard",limit=limit,
        )
    except CatalogError:
        failures.append("Personal radio is temporarily unavailable")
    # A new-release record is NOT a MusicBrainz recording. Do not try to add
    # it to catalog_candidates, sendAudio or queue_items.
    return {
        "week": current.isocalendar().week,
        "year": current.isocalendar().year,
        "as_of": current.isoformat(),
        "seed_artists": seeds,
        "releases": [r.public() for r in releases],
        "tracks": [t.public() for t in songs],
        "type": "metadata_only",
        "warnings": failures,
    }
