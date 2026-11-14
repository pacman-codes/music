"""AniList chat handlers register without exposing video links."""
from aiogram import Router

from music_bot.storage import Library
from music_bot.telegram_anime import format_anime,install_anime_handlers
from music_bot.anime import AnimeTitle


def test_bot_anime_commands_and_metadata_only():
    row=AnimeTitle(1,"Cowboy Bebop","Cowboy Bebop",None,26,"TV","FINISHED",1998,87,12)
    summary=format_anime([row])
    assert "#1 Cowboy Bebop" in summary
    assert "без видео" in summary


def test_anime_routes_registered(tmp_path):
    db=Library(tmp_path/"db.sqlite")
    router=Router()
    install_anime_handlers(router,db,object())
    assert len(router.message.handlers)==5
