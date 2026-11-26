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
    assert len(router.message.handlers)==3
    assert len(router.callback_query.handlers)==1


def test_audio_controls_have_all_five_reactions_and_next():
    from music_bot.telegram_youtube import audio_reaction_keyboard
    kb = audio_reaction_keyboard(7)
    callbacks = {b.callback_data for row in kb.inline_keyboard for b in row}
    assert callbacks == {"nav:discover"} | {
        f"vset:l:7:{status}" for status in ("add", "maybe", "later", "never", "interesting")
    }


def test_discovery_finds_source_without_manual_query_but_requires_choice(tmp_path):
    import asyncio
    from music_bot.catalog import CatalogTrack
    from music_bot.telegram_youtube import offer_discovery_audio
    mbid = "8a49dba0-253a-4535-b87f-78bb035336ce"
    db = Library(tmp_path / "music.db")
    seed = db.add_track("file", "uid", "Known", "Seed Artist")
    db.like(seed.id)

    class Catalog:
        async def artist_radio(self, name, *, difficulty, limit):
            return [CatalogTrack(mbid, "Fresh Song", "New Artist", duration_ms=100000)]

    class Source:
        async def search(self, query, *, limit):
            assert query == "New Artist Fresh Song"
            return [VideoCandidate(ID, "New Artist - Fresh Song", "New Artist", 100)]

    class Bot:
        def __init__(self):
            self.messages = []
        async def send_message(self, chat_id, text, **kwargs):
            assert chat_id == 42
            self.messages.append((text, kwargs))

    bot = Bot()
    asyncio.run(offer_discovery_audio(bot, db, 42, Catalog(), Source()))
    text, kwargs = bot.messages[-1]
    assert "Fresh Song" in text
    assert "разрешена" in text
    callbacks = {b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row}
    assert f"ytget:{ID}:{mbid}" in callbacks
    assert "nav:discover" in callbacks
    assert db.get_candidate(mbid)
    assert db.get_youtube_video(ID)
    assert len(db.all_tracks()) == 1  # no silent import, no fake file
