import array
import math
import shutil
import subprocess
import wave

import pytest
from fastapi.testclient import TestClient

from music_bot.native_api import create_app
from music_bot.native_media import MediaProcessor, RATE, sample_features, distance
from music_bot.native_storage import NativeStorage
from music_bot.recommend import track_weight
from music_bot.storage import Library


def tone(freq, seconds=3):
    return array.array("h",(round(15000*math.sin(2*math.pi*freq*i/RATE))
                            for i in range(RATE*seconds))).tobytes()


def test_acoustic_descriptors_are_measured_not_genre_labels():
    low=sample_features(tone(80))
    high=sample_features(tone(2200))
    assert low["low"] > high["low"]
    assert high["high"] > low["high"]
    assert distance(low,low)==0
    assert distance(low,high)>.1
    assert low["tempo_estimate"] is None
    with pytest.raises(ValueError):
        sample_features(b"\0"*(RATE*2))


def test_acoustic_ranking_is_bounded_and_neutral_for_low_rating(tmp_path):
    db=Library(tmp_path/"music.db")
    track=db.add_track("f","u","Track","Artist")
    args=(track,set(),set(),{})
    baseline=track_weight(*args)
    assert track_weight(*args,acoustic_affinity={track.id:10})==pytest.approx(baseline*1.2)
    assert track_weight(*args,ratings={track.id:2},acoustic_affinity={track.id:1})==track_weight(*args,ratings={track.id:2})


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg unavailable")
def test_real_audio_transcoding_range_and_similarity(tmp_path,monkeypatch):
    import random
    import os
    from collections import namedtuple
    root=tmp_path/"lain"
    db=Library(root/"music.db")
    store=NativeStorage(db,root/"audio")
    media=MediaProcessor(store)
    # Lossless noise has a much larger file than its 96 kb/s AAC derivative.
    rng=random.Random(1)
    def import_pcm(name, raw):
        source=tmp_path/(name+".wav")
        with wave.open(str(source),"wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(raw)
        output=tmp_path/(name+".flac")
        subprocess.run(["ffmpeg","-v","error","-nostdin","-i",str(source),"-c:a","flac",str(output)],check=True,timeout=30)
        return store.import_file(output,title=name,artist="Band")
    a=import_pcm("noise",array.array("h",(rng.randrange(-18000,18000) for _ in range(44100*2*3))).tobytes())
    b=import_pcm("tone",array.array("h",(round(10000*math.sin(2*math.pi*120*i/44100))
                            for i in range(44100*2*3))).tobytes())
    usage=namedtuple("Usage","total used free")
    monkeypatch.setattr("music_bot.native_media.shutil.disk_usage",lambda _:usage(10**10,0,10**10))
    media.request([a,b])
    for _ in range(8):
        if not media.run_once():break
    ai=media.info(a)
    assert ai["analysis_state"]=="ready" and ai["economy_state"]=="ready",ai
    assert ai["economy_representation"]=="economy"
    assert ai["analysis"]["duration_ms"]==pytest.approx(3000,abs=100)
    assert ai["economy_size"]<store.audio(a)[2]
    assert media.info(b)["analysis_state"]=="ready"
    assert media.similar(a)[0][0]==b
    db.set_rating(a,9)
    assert b in media.affinity()
    db.set_track_verdict(b,"never")
    assert media.similar(a)==[] and b not in media.affinity()
    original=store.audio(a)[0].read_bytes()
    with TestClient(create_app(root)) as client:
        for path in (f"/v1/tracks/{a}/analysis",f"/v1/tracks/{a}/similar",f"/v1/tracks/{a}/audio?quality=economy"):
            assert client.get(path).status_code==401
        code=store.issue_pairing()
        token=client.post("/v1/pair",json={"code":code,"label":"Phone"}).json()["token"]
        headers={"Authorization":"Bearer "+token}
        response=client.get(f"/v1/tracks/{a}/audio?quality=economy",headers={**headers,"Range":"bytes=0-15"})
        assert response.status_code==206
        assert response.headers["x-lain-representation"]=="economy"
        assert response.headers["content-type"]=="audio/mp4"
        assert client.get(f"/v1/tracks/{a}/audio",headers=headers).content==original
        assert client.get(f"/v1/tracks/{a}/analysis",headers=headers).json()["analysis_state"]=="ready"
        assert client.get(f"/v1/tracks/{a}/audio?quality=bad",headers=headers).status_code==422
        variant=media.asset(a)[0]
        variant.unlink()
        assert client.get(f"/v1/tracks/{a}/audio?quality=economy",headers=headers).status_code==409
        state=client.post("/v1/media/prepare",headers=headers,json={"track_ids":[a]}).json()["results"][0]
        assert state["economy_state"]=="pending"
        assert media.run_once()
        assert media.info(a)["economy_state"]=="ready"
    assert store.audio(a)[0].read_bytes()==original
