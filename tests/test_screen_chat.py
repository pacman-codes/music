from aiogram import Router
from music_bot.storage import Library
from music_bot.telegram_screen import install_screen_handlers


def test_film_commands_registered(tmp_path):
    db=Library(tmp_path/"db.sqlite")
    router=Router()
    install_screen_handlers(router,db,object())
    assert len(router.message.handlers)==5
