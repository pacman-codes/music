from aiogram import Router
from music_bot.storage import Library
from music_bot.telegram_fingerprint import install_fingerprint_handlers

def test_chat_route_registered_with_off_default(tmp_path):
    db=Library(tmp_path/"db.sqlite")
    router=Router()
    install_fingerprint_handlers(router,db,None)
    assert len(router.message.handlers)==1
