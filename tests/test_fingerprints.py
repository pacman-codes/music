"""Fingerprint inference remains opt-in and never assigns identities by confidence."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import music_bot.fingerprints as fingerprints
from music_bot.fingerprints import (
    AudioRecognition, RecognitionCandidate, RecognitionError,
    RecognitionResult, parse_acoustid, parse_fpcalc_output,
    recognize_telegram_track,
)
from music_bot.catalog import CatalogTrack
from music_bot.storage import Library


MBID="8a49dba0-253a-4535-b87f-78bb035336ce"


def response():
    return {"status":"ok","results":[
        {"score":0.92,"recordings":[{
            "id":MBID,"title":"Roads",
            "artists":[{"name":"Portishead"}],
        }]},
        {"score":0.81,"recordings":[{
            "id":MBID,"title":"Roads",
            "artists":[{"name":"Portishead"}],
        }]},
        {"score":0.2,"recordings":[{
            "id":"f3bba4cd-8018-468b-902e-bc8f029593e5",
            "title":"Wrong song","artists":[{"name":"Other"}],
        }]},
    ]}


def test_fpcalc_response_and_acoustid_candidate_parsing():
    assert parse_fpcalc_output(json.dumps({
        "duration":221.2,"fingerprint":"A"*120,
    }).encode())==(221,"A"*120)
    with pytest.raises(RecognitionError):
        parse_fpcalc_output(b"<html>bad</html>")
    with pytest.raises(RecognitionError):
        parse_fpcalc_output(json.dumps({"duration":2,"fingerprint":"bad"}).encode())
    found=parse_acoustid(response())
    assert len(found)==1
    assert found[0].recording.mbid==MBID
    assert found[0].confidence==0.92
    assert found[0].public()["playable"] is False
    assert found[0].public()["match_confirmed"] is False


def test_acoustid_post_is_server_side_key_and_cached(tmp_path,monkeypatch):
    calls=[]
    def handle(request):
        calls.append(request)
        assert request.url.host=="api.acoustid.org"
        assert request.url.path=="/v2/lookup"
        assert "SECRET" not in str(request.url)
        assert request.method=="POST"
        assert b"client=SECRET" in request.content
        assert b"fingerprint=" in request.content
        return httpx.Response(200,json=response())
    async def fake_fpcalc(_):
        return 221,"A"*120
    monkeypatch.setattr(fingerprints,"fpcalc",fake_fpcalc)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            source=AudioRecognition(tmp_path/"db.sqlite","SECRET",http)
            file=tmp_path/"song.mp3"
            file.write_bytes(b"ID3"+b"\0"*100)
            one=await source.identify(file)
            two=await source.identify(file)
            assert one==two
            assert len(one.candidates)==1
            assert one.duration_sec==221
        assert len(calls)==1
    asyncio.run(scenario())


def test_fingerprint_exact_digest_duplicate_suggestions_not_auto_merges(tmp_path):
    db=Library(tmp_path/"music.sqlite")
    a=db.add_track("one","unique-one","Roads","Portishead")
    b=db.add_track("two","unique-two","Roads","Portishead")
    digest="a"*64
    assert db.save_audio_fingerprint(a.id,digest,220)==[]
    assert db.save_audio_fingerprint(b.id,digest,221)==[a.id]
    assert db.get_track(a.id) and db.get_track(b.id)
    assert a.id != b.id
    assert db.recording_for_track(a.id) is None
    for bad in ("","not-a-hash","g"*64):
        with pytest.raises(ValueError):
            db.save_audio_fingerprint(a.id,bad,200)


def test_telegram_download_bound_and_temporary_cleanup(tmp_path):
    db=Library(tmp_path/"music.db")
    track=db.add_track("tg-file","tg-unique","Roads","Portishead")
    local_paths=[]
    class Bot:
        async def get_file(self, file_id):
            assert file_id=="tg-file"
            return SimpleNamespace(file_size=100,file_path="trusted/telegram/file")
        async def download_file(self, name, *, destination, timeout):
            assert name=="trusted/telegram/file"
            assert timeout==90
            path=Path(destination)
            local_paths.append(path)
            path.write_bytes(b"ID3"+b"\0"*100)
    class Recognizer:
        async def identify(self,path):
            assert path.exists()
            return RecognitionResult("a"*64,201,(
                RecognitionCandidate(CatalogTrack(MBID,"Roads","Portishead"),0.91),
            ))
    result=asyncio.run(recognize_telegram_track(db,Bot(),Recognizer(),track.id))
    assert result["track_id"]==track.id
    assert result["auto_matched"] is False
    assert result["results"][0]["mbid"]==MBID
    assert not local_paths[0].exists()
    assert db.get_candidate(MBID)["mbid"]==MBID
    assert db.recording_for_track(track.id) is None

    class HugeBot(Bot):
        async def get_file(self,file_id):
            return SimpleNamespace(file_size=20*1024*1024,file_path="path")
        async def download_file(self,*args,**kwargs):
            raise AssertionError("must not download huge file")
    with pytest.raises(RecognitionError,match="19 МиБ"):
        asyncio.run(recognize_telegram_track(db,HugeBot(),Recognizer(),track.id))
