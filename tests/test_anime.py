"""Offline AniList API contract and private anime watchlist tests."""
import asyncio
import httpx
import pytest

from music_bot.anime import AniListCatalog, AniListError, parse_anime
from music_bot.storage import Library


def sample(id=1, title="Cowboy Bebop"):
    return {"id":id,"idMal":12,"title":{"english":title,"romaji":title,"native":"カウボーイ"},
            "episodes":26,"format":"TV","status":"FINISHED","seasonYear":1998,
            "averageScore":87}


def test_anime_parse_and_invalid_entries():
    row=parse_anime(sample())
    assert row.title=="Cowboy Bebop"
    assert row.episodes==26 and row.year==1998
    assert row.public()["playable"] is False
    assert parse_anime({}) is None
    assert parse_anime({"id":"1","title":{"english":"Invalid"}}) is None
    invalid=sample()
    invalid["episodes"]=-1
    invalid["averageScore"]=105
    result=parse_anime(invalid)
    assert result.episodes is None
    assert result.average_score is None


def test_anilist_graphql_only_fixed_query_variables_and_cache(tmp_path):
    seen=[]
    def handle(request):
        seen.append(request)
        assert request.url.host=="graphql.anilist.co"
        assert request.method=="POST"
        assert request.headers["content-type"]=="application/json"
        body=__import__("json").loads(request.content)
        assert body["variables"]=={"search":"Cowboy Bebop","perPage":10}
        assert 'type: ANIME' in body["query"]
        assert 'isAdult: false' in body["query"]
        return httpx.Response(200,json={"data":{"Page":{"media":[sample()]}}})
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            service=AniListCatalog(tmp_path/"anime.db",http)
            rows=await service.search("Cowboy Bebop",10)
            assert rows[0].id==1
            assert await service.search("Cowboy Bebop",10)==rows
        assert len(seen)==1
    asyncio.run(scenario())


def test_bad_anilist_status_or_untrusted_payload_is_rejected(tmp_path):
    for response in (
        httpx.Response(429,json={"errors":[{"message":"rate limit"}]}),
        httpx.Response(302,headers={"Location":"https://untrusted.example.org"}),
        httpx.Response(200,headers={"content-type":"text/html"},text="not JSON"),
        httpx.Response(200,json={"data":None,"errors":[{"message":"server"}]}),
    ):
        async def scenario():
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(lambda req: response)
            ) as http:
                client=AniListCatalog(tmp_path/"anilist.db",http)
                with pytest.raises(AniListError):
                    await client.search("Cowboy Bebop")
        asyncio.run(scenario())


def test_watchlist_progress_rating_and_persistence(tmp_path):
    path=tmp_path/"music.db"
    db=Library(path)
    from music_bot.anime import AnimeTitle
    anime=AnimeTitle(1,"Cowboy Bebop","Cowboy Bebop",None,26,"TV","FINISHED",1998,87,12)
    db.remember_anime([anime])
    entry=db.update_anime_watch(1,watch_status="watching",progress=6,score=9)
    assert entry["progress"]==6 and entry["score"]==9
    assert Library(path).anime_list()[0]["watch_status"]=="watching"
    with pytest.raises(ValueError): db.update_anime_watch(1,progress=27)
    with pytest.raises(ValueError): db.update_anime_watch(1,score=11)
    with pytest.raises(ValueError): db.update_anime_watch(1,watch_status="invalid")
    with pytest.raises(ValueError): db.update_anime_watch(555,watch_status="planned")
    assert db.remove_anime_watch(1)
    assert db.anime_list()==[]
