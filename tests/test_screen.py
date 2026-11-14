"""TMDb movie+TV catalog: read bearer only, no streaming, persistence."""
import asyncio
import json

import httpx
import pytest

from music_bot.screen import TmdbCatalog,TmdbError,parse_screen_title
from music_bot.storage import Library

MOVIE={"id":157336,"media_type":"movie","title":"Interstellar",
       "original_title":"Interstellar","release_date":"2014-11-05",
       "overview":"Space travel","vote_average":8.7,"adult":False}
TV={"id":1399,"media_type":"tv","name":"Game of Thrones",
    "original_name":"Game of Thrones","first_air_date":"2011-04-17",
    "overview":"TV Series","vote_average":8.5}


def test_parser_filters_people_and_adult_and_dates():
    assert parse_screen_title(MOVIE).title=="Interstellar"
    assert parse_screen_title(TV).media_type=="tv"
    assert parse_screen_title({"media_type":"person","id":1,"name":"Alice"}) is None
    assert parse_screen_title({**MOVIE,"adult":True}) is None
    assert parse_screen_title({**MOVIE,"id":"oops"}) is None
    assert parse_screen_title({**MOVIE,"release_date":"unknown"}).release_year is None


def test_search_sends_bearer_only_as_header_and_uses_cache(tmp_path):
    requests=[]
    def handle(req):
        requests.append(req)
        assert req.url.host=="api.themoviedb.org"
        assert req.url.path=="/3/search/multi"
        assert req.url.params["query"]=="Interstellar"
        assert req.url.params.get("api_key") is None
        assert req.headers["authorization"]=="Bearer SECRET_TEST_TOKEN"
        return httpx.Response(200,json={
            "results":[MOVIE,TV,{"id":11,"media_type":"person","name":"Actor"}]})
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            catalog=TmdbCatalog(tmp_path/"db.sqlite","SECRET_TEST_TOKEN",http)
            result=await catalog.search("Interstellar")
            assert len(result)==2
            assert result[0].public()["playable"] is False
            assert await catalog.search("Interstellar")==result
        assert len(requests)==1
    asyncio.run(exercise())


def test_bad_key_and_redirect_rejected(tmp_path):
    for response in (
        httpx.Response(401,json={"status_message":"unauthorized"}),
        httpx.Response(429,json={"status_message":"rate limit"}),
        httpx.Response(302,headers={"Location":"https://bad.example.com"}),
        httpx.Response(200,headers={"content-type":"text/html"},text="<html>"),
    ):
        async def exercise():
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(lambda req: response)
            ) as http:
                client=TmdbCatalog(tmp_path/"db.sqlite","VALID",http)
                with pytest.raises(TmdbError):
                    await client.search("Movie title")
        asyncio.run(exercise())


def test_disabled_without_tmdb_token(tmp_path):
    catalog=TmdbCatalog(tmp_path/"db.sqlite","")
    async def exercise():
        with pytest.raises(TmdbError):
            await catalog.search("Movie title")
        await catalog.close()
    asyncio.run(exercise())


def test_private_movies_and_tv_progress_persist(tmp_path):
    db=Library(tmp_path/"db.sqlite")
    from music_bot.screen import ScreenTitle
    movie=parse_screen_title(MOVIE)
    tv=parse_screen_title(TV)
    db.remember_screen_titles([movie,tv])
    assert db.update_screen_watch("movie",movie.id,watch_status="completed",score=9)["score"]==9
    assert db.update_screen_watch("tv",tv.id,watch_status="watching",
                                   season=2,episode=5,score=8)["episode"]==5
    assert len(Library(db.path).screen_watchlist())==2
    with pytest.raises(ValueError):
        db.update_screen_watch("movie",movie.id,watch_status="watching",season=1)
    with pytest.raises(ValueError):
        db.update_screen_watch("tv",tv.id,watch_status="broken")
    with pytest.raises(ValueError):
        db.update_screen_watch("movie",999999,watch_status="planned")
    assert db.remove_screen_watch("movie",movie.id)
    assert len(db.screen_watchlist())==1
