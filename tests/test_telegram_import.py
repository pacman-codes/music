"""Chat /import parsing, no network and no credentials."""
from aiogram import Router

from music_bot.storage import Library
from music_bot.telegram_import import install_import_handlers, parse_import_command

MBID="8a49dba0-253a-4535-b87f-78bb035336ce"


def test_import_parser_requires_mb_identity_and_url():
    url="https://audio.example.org/track.mp3?signature=abc"
    assert parse_import_command(f"/import {MBID} {url}")==(MBID,url)
    assert parse_import_command(f"/import@music_bot {MBID} {url}")==(MBID,url)
    for bad in [
        f"/import {MBID}",
        f"/import bad {url}",
        f"/import {MBID} ",
        f"/find {MBID} {url}",
        f"/import {MBID} " + "a"*2050,
        "",
    ]:
        assert parse_import_command(bad) is None


def test_command_registered_under_owner_router(tmp_path):
    db=Library(tmp_path/"music.db")
    router=Router()
    install_import_handlers(router,db,owner_id=42,allowed_hosts=())
    assert len(router.message.handlers)==1
