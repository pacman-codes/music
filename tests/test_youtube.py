"""Networkless YouTube search, deduplication, limits, and Telegram asset semantics."""
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

import music_bot.youtube as yt
from music_bot.storage import Library

VIDEO="dQw4w9WgXcQ"
VIDEO2="kJQP7kiw5Fk"


def test_search_parser_rejects_long_live_invalid_and_duplicate_videos():
    response={"entries":[
        {"id":VIDEO,"title":"My song","channel":"My channel","duration":240},
        {"id":VIDEO,"title":"Duplicate","duration":240},
        {"id":"bad","title":"Bad ID","duration":60},
        {"id":VIDEO2,"title":"Long video","duration":2400},
        {"id":VIDEO2,"title":"Live","live_status":"is_live"},
        {"id":VIDEO2,"title":"Track Two","duration":165},
    ]}
    result=yt.parse_search_response(response)
    assert [v.video_id for v in result]==[VIDEO,VIDEO2]
    assert result[0].channel=="My channel"
    assert result[1].duration_sec==165
    assert all(v.public()["playable"] is False for v in result)
    with pytest.raises(yt.YouTubeError):
        yt.parse_search_response({"wrong":"format"})
    for bad in ("", "not a youtube id", "https://youtube.com/watch?v="+VIDEO):
        with pytest.raises(yt.YouTubeError):
            yt.validate_video_id(bad)


def test_youtube_search_passes_single_safe_argument(monkeypatch):
    commands=[]
    async def fake_run(args,*,timeout,output_dir=None):
        commands.append(args)
        return json.dumps({"entries":[{
            "id":VIDEO,"title":"My song","duration":160,"uploader":"Creator"}]
        }).encode("utf-8")
    monkeypatch.setattr(yt,"run_ytdlp",fake_run)
    async def scenario():
        source=yt.YouTubeSource()
        rows=await source.search("Portishead Roads",limit=6)
        assert rows[0].title=="My song"
        assert rows[0].channel=="Creator"
        with pytest.raises(ValueError):
            await source.search("--output /etc/passwd "*50)
    asyncio.run(scenario())
    assert len(commands)==1
    assert commands[0][-1]=="ytsearch6:Portishead Roads"
    assert "--ignore-config" in commands[0]
    assert "--no-playlist" not in commands[0]


def test_youtube_candidate_ingest_reuses_telegram_file_and_cleans_up(tmp_path,monkeypatch):
    db=Library(tmp_path/"music.db")
    db.remember_youtube_videos([yt.VideoCandidate(VIDEO,"Song","Creator",180)])
    used=[]
    paths=[]

    @asynccontextmanager
    async def fake_download(video_id):
        assert video_id==VIDEO
        path=tmp_path/"audio.mp3"
        path.write_bytes(b"ID3"+b"\0"*100)
        paths.append(path)
        try:
            yield path
        finally:
            path.unlink(missing_ok=True)

    async def publish(path,title,channel):
        assert path.exists()
        used.append((title,channel))
        return "TG_FILE_ID","TG_UNIQUE_ID"

    monkeypatch.setattr(yt,"temporary_youtube_mp3",fake_download)

    async def scenario():
        svc=yt.YouTubeIngestService(db,publish)
        first=await svc.import_video(VIDEO)
        second=await svc.import_video(VIDEO)
        assert first["reused"] is False and second["reused"] is True
        assert first["track_id"]==second["track_id"]
    asyncio.run(scenario())
    assert used==[("Song","Creator")]
    assert not paths[0].exists()
    saved=db.get_youtube_video(VIDEO)
    assert saved["track_id"] is not None
    assert db.get_track(saved["track_id"]).file_id=="TG_FILE_ID"


def test_youtube_does_not_download_unsearched_video(tmp_path):
    db=Library(tmp_path/"music.db")
    async def forbidden(*args):
        raise AssertionError("must not upload")
    async def scenario():
        svc=yt.YouTubeIngestService(db,forbidden)
        with pytest.raises(yt.YouTubeError):
            await svc.import_video(VIDEO)
    asyncio.run(scenario())


def test_invalid_file_never_becomes_telegram_audio(tmp_path,monkeypatch):
    @asynccontextmanager
    async def empty_file_download(vid):
        path=tmp_path/"not_an_mp3.html"
        path.write_text("<html>Not music</html>")
        try:
            yield path
        finally:
            path.unlink()
    monkeypatch.setattr(yt,"temporary_youtube_mp3",empty_file_download)
    db=Library(tmp_path/"db.sqlite")
    db.remember_youtube_videos([yt.VideoCandidate(VIDEO,"Song","Creator",120)])
    async def fail_upload(*args):
        raise yt.YouTubeError("Failed")
    async def scenario():
        with pytest.raises(yt.YouTubeError):
            await yt.YouTubeIngestService(db,fail_upload).import_video(VIDEO)
    asyncio.run(scenario())
    assert db.get_youtube_video(VIDEO)["track_id"] is None


def test_candidate_retrieval_and_link_integrity(tmp_path):
    db=Library(tmp_path/"music.db")
    db.remember_youtube_videos([yt.VideoCandidate(VIDEO,"Song","Creator",120)])
    with pytest.raises(ValueError):
        db.link_youtube_video(VIDEO,999)
    t=db.add_track("file","unique","Song","Creator")
    db.link_youtube_video(VIDEO,t.id)
    assert db.get_youtube_video(VIDEO)["track_id"]==t.id
    db.remember_youtube_videos([yt.VideoCandidate(VIDEO,"Updated","Creator",120)])
    assert db.get_youtube_video(VIDEO)["title"]=="Updated"
    assert db.get_youtube_video(VIDEO)["track_id"]==t.id
