"""Weekly ListenBrainz releases are metadata-only, prioritizing rated artists."""
import asyncio
from datetime import date

from music_bot.catalog import CatalogTrack
from music_bot.storage import Library
from music_bot.weekly import parse_fresh_releases, weekly_discovery

A="8a49dba0-253a-4535-b87f-78bb035336ce"
B="f3bba4cd-8018-468b-902e-bc8f029593e5"
C="8e74dd9d-e5a3-4acd-918a-c36a0f8cda84"


def response():
    return {"payload":{"releases":[
        {"release_group_mbid":A,"release_name":"New Album",
         "artist_credit_name":"Portishead","release_date":"2026-11-12",
         "release_group_primary_type":"Album"},
        {"release_group_mbid":B,"release_name":"Another New Album",
         "artist_credit_name":"Stranger","release_date":"2026-11-13",
         "release_group_primary_type":"EP"},
        {"release_group_mbid":C,"release_name":"Tomorrow",
         "artist_credit_name":"Portishead","release_date":"2026-11-15"},
        {"release_group_mbid":A,"release_name":"Duplicate",
         "artist_credit_name":"Portishead","release_date":"2026-11-12"},
    ]}}


def test_releases_are_deduplicated_scored_and_not_playable():
    rows=parse_fresh_releases(response(),["Portishead"],
                              today=date(2026,11,14),limit=10)
    assert {r.mbid for r in rows}=={A,B}
    assert rows[0].artist=="Portishead"
    assert rows[0].favorite_artist is True
    assert rows[0].public()["playable"] is False
    assert rows[0].public()["type"]=="release_metadata"


class FakeCatalog:
    def __init__(self):
        self.queries=[]
    async def _json(self,provider,path,params,ttl):
        self.queries.append((provider,path,params,ttl))
        return response()
    async def artist_radio(self,artist,*,difficulty,limit):
        assert artist=="Portishead"
        assert difficulty=="hard"
        return [CatalogTrack(B,"Unheard Song","Other Group")]


def test_weekly_digest_prioritizes_owner_and_never_claims_audio(tmp_path):
    db=Library(tmp_path/"music.db")
    loved=db.add_track("file1","uid1","Roads","Portishead")
    db.like(loved.id)
    db.set_rating(loved.id,10)
    catalog=FakeCatalog()
    payload=asyncio.run(weekly_discovery(
        db,catalog,limit=5,today=date(2026,11,14),
    ))
    assert payload["week"]==46
    assert payload["year"]==2026
    assert payload["type"]=="metadata_only"
    assert payload["seed_artists"]==["Portishead"]
    assert payload["releases"][0]["artist"]=="Portishead"
    assert payload["tracks"][0]["title"]=="Unheard Song"
    assert payload["tracks"][0]["playable"] is False
    assert db.resolved_catalog_tracks()=={}
    assert catalog.queries[0][1]=="/1/explore/fresh-releases/"


def test_weekly_with_no_user_profile_still_lists_sitewide_releases(tmp_path):
    db=Library(tmp_path/"db.sqlite")
    found=asyncio.run(weekly_discovery(
        db,FakeCatalog(),today=date(2026,11,14),
    ))
    assert found["seed_artists"]==[]
    assert found["tracks"]==[]
    assert len(found["releases"])==2
