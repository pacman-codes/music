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
    assert len(router.message.handlers)==4
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


def test_plain_query_reuses_exact_cached_audio(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from music_bot.telegram_youtube import plain_music_search, cached_audio_for_query
    db = Library(tmp_path / "music.db")
    track = db.add_track("real-file", "unique", "Artist - Song", "Artist")
    assert cached_audio_for_query(db, "Song Artist").id == track.id
    assert cached_audio_for_query(db, "Artist — Song (live)") is None

    class Bot:
        async def send_audio(self, chat_id, file, **kwargs):
            assert chat_id == 42 and file == "real-file"
            assert kwargs["reply_markup"]

    class Source:
        async def search(self, *args, **kwargs):
            raise AssertionError("Do not search or download cached files")

    msg = SimpleNamespace(text="Artist Song", chat=SimpleNamespace(id=42))
    asyncio.run(plain_music_search(msg, Bot(), db, Source(),
                                   search_enabled=True, download_enabled=True))


def test_plain_query_searches_without_command_and_offers_sources(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from music_bot.telegram_youtube import plain_music_search
    db = Library(tmp_path / "music.db")

    class Source:
        async def search(self, query, *, limit):
            assert query == "Artist Song"
            return [VideoCandidate(ID, "Artist Song", "Artist", 100)]

    answers = []
    async def answer(text, **kwargs):
        answers.append((text, kwargs))
    msg = SimpleNamespace(text="Artist Song", chat=SimpleNamespace(id=42), answer=answer)
    asyncio.run(plain_music_search(msg, None, db, Source(),
                                   search_enabled=True, download_enabled=True))
    markup = answers[-1][1]["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == f"ytget:{ID}"
    assert markup.inline_keyboard[0][1].url.startswith("https://www.youtube.com/watch?")
    assert db.get_youtube_video(ID)
    assert not db.all_tracks()


def test_album_search_and_ordered_tracklist(tmp_path):
    import asyncio
    from music_bot.albums import album_query, find_albums, album_tracks
    from music_bot.telegram_youtube import album_page
    from music_bot.catalog import CatalogTrack
    a = "8a49dba0-253a-4535-b87f-78bb035336ce"
    b = "f3bba4cd-8018-468b-902e-bc8f029593e5"
    c = "8e74dd9d-e5a3-4acd-918a-c36a0f8cda84"
    credit = [{"name": "Artist"}]
    release = {"id": a, "title": "Album Name", "artist-credit": credit,
               "release-group": {"id": a, "primary-type": "Album"},
               "track-count": 2, "status": "Official"}

    class Catalog:
        async def _json(self, provider, path, params, **kw):
            if path == "/ws/2/release/":
                return {"releases": [release]}
            assert path == "/ws/2/release/" + a
            return {**release, "media": [{"tracks": [
                {"title": "Second in alphabet", "recording": {"id": b}},
                {"title": "First in alphabet", "recording": {"id": c}},
            ]}]}

    assert album_query("Artist альбом Album Name") == ("Artist Album Name", True)
    catalog = Catalog()
    albums = asyncio.run(find_albums(catalog, "Artist Album Name", exact=True))
    assert [x.mbid for x in albums] == [a]
    assert asyncio.run(find_albums(catalog, "Wrong Artist Album Name", exact=True)) == []
    title, artist, tracks = asyncio.run(album_tracks(catalog, a))
    assert [x.mbid for x in tracks] == [b, c]
    db = Library(tmp_path / "music.db")
    text, markup = album_page(db, a, title, artist, tracks, 0)
    assert "1. Second" in text and "2. First" in text
    callbacks = {v.callback_data for row in markup.inline_keyboard for v in row}
    assert f"albtrack:{b}" in callbacks and f"albumplay:{a}" not in callbacks
    db.remember_candidates(tracks)
    for i, tr in enumerate(tracks):
        saved = db.add_track(f"file{i}", f"uid{i}", tr.title, tr.artist)
        db.confirm_recording_match(saved.id, tr.mbid)
    _, markup = album_page(db, a, title, artist, tracks, 0)
    assert f"albumplay:{a}" in {v.callback_data for row in markup.inline_keyboard for v in row}
