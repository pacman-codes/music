"""Direct artist MBID radio uses ListenBrainz without a fuzzy artist-name lookup."""
import asyncio

import httpx
import pytest

from music_bot.catalog import MusicCatalog

ARTIST="8f6bd1e4-fbe1-4f50-aa9b-94c450ec0f11"
RECORDING="8a49dba0-253a-4535-b87f-78bb035336ce"


def test_radio_by_confirmed_artist_id(tmp_path):
    calls=[]
    def upstream(request):
        calls.append(request.url)
        if request.url.host=="api.listenbrainz.org":
            assert request.url.path==f"/1/lb-radio/artist/{ARTIST}"
            return httpx.Response(200,json={
                ARTIST:[{"recording_mbid":RECORDING}]
            })
        assert request.url.host=="musicbrainz.org"
        assert request.url.path=="/ws/2/recording/"
        return httpx.Response(200,json={"recordings":[{
            "id":RECORDING,"title":"Roads",
            "artist-credit":[{"name":"Portishead","artist":{
                "id":ARTIST,"name":"Portishead"}}]
        }]})
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(upstream)
        ) as http:
            client=MusicCatalog(tmp_path/"cache.db",http)
            songs=await client.artist_radio_by_id(ARTIST,limit=3)
            assert [t.title for t in songs]==["Roads"]
            assert len(calls)==2 # No MusicBrainz artist search request.
            for bad in ("invalid",""):
                with pytest.raises(ValueError):
                    await client.artist_radio_by_id(bad)
    asyncio.run(scenario())
