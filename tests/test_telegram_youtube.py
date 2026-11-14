"""Telegram YouTube controls remain inaccessible until feature gates are enabled."""
from aiogram import Router
from music_bot.telegram_youtube import install_youtube_handlers, youtube_keyboard
from music_bot.youtube import VideoCandidate
from music_bot.storage import Library

ID="dQw4w9WgXcQ"

def test_buttons_only_when_download_enabled():
    videos=[VideoCandidate(ID,"Video title","Uploader",100)]
    assert youtube_keyboard(videos,False) is None
    kb=youtube_keyboard(videos,True)
    assert kb.inline_keyboard[0][0].callback_data==f"ytget:{ID}"

def test_owner_protected_routes_are_registered(tmp_path):
    db=Library(tmp_path/"music.db")
    router=Router()
    install_youtube_handlers(router,db,42,
                             search_enabled=False,download_enabled=False)
    assert len(router.message.handlers)==2
    assert len(router.callback_query.handlers)==1
