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


def test_album_artist_and_title_are_both_required():
    import asyncio
    from music_bot.albums import find_albums
    wanted = "4faf8ad3-4169-4a0f-8dd0-50da0dfc8c91"
    def release(mbid, artist, title, count=13):
        return {"id": mbid, "title": title, "artist-credit": [{"name": artist}],
                "release-group": {"id": mbid, "primary-type": "Album"},
                "track-count": count, "status": "Official"}
    wrong = release("8a49dba0-253a-4535-b87f-78bb035336ce", "Jude Frankum", "SOMA", 2)
    right = release(wanted, "Skrillex", "SOMA")
    other_title = release("f3bba4cd-8018-468b-902e-bc8f029593e5", "Skrillex", "Other Album")

    class Catalog:
        async def _json(self, provider, path, params, **kwargs):
            assert provider == "mb" and path == "/ws/2/release/"
            assert '(artist:"Skrillex" AND release:"SOMA")' in params["query"]
            assert '(artist:"SOMA" AND release:"Skrillex")' in params["query"]
            return {"releases": [wrong, other_title, right]}

    for exact in (True, False):
        rows = asyncio.run(find_albums(Catalog(), "Skrillex SOMA", exact=exact))
        assert [r.mbid for r in rows] == [wanted]

    class NoMatch:
        async def _json(self, *args, **kwargs):
            return {"releases": [wrong, other_title]}

    assert asyncio.run(find_albums(NoMatch(), "Skrillex SOMA", exact=False)) == []


def test_album_search_handles_multiword_artist_and_reversed_order():
    import asyncio
    from music_bot.albums import find_albums
    release = {"id": "8a49dba0-253a-4535-b87f-78bb035336ce", "title": "Mezzanine",
               "artist-credit": [{"name": "Massive Attack"}],
               "release-group": {"primary-type": "Album"},
               "track-count": 11, "status": "Official"}
    class Catalog:
        async def _json(self, provider, path, params, **kwargs):
            assert '(artist:"Massive Attack" AND release:"Mezzanine")' in params["query"]
            return {"releases": [release]}
    for query in ("Massive Attack Mezzanine", "Mezzanine Massive Attack"):
        assert len(asyncio.run(find_albums(Catalog(), query, exact=False))) == 1


def test_exact_album_opens_directly_without_youtube(tmp_path):
    import asyncio
    import uuid
    from types import SimpleNamespace
    from music_bot.telegram_youtube import plain_music_search
    db = Library(tmp_path / "music.db")
    mbid = str(uuid.UUID(int=100))
    credit = [{"name": "Skrillex"}]
    release = {"id": mbid, "title": "SOMA", "artist-credit": credit,
               "release-group": {"id": mbid, "primary-type": "Album"},
               "track-count": 13, "status": "Official"}

    class Catalog:
        async def _json(self, provider, path, params, **kwargs):
            if path == "/ws/2/release/":
                return {"releases": [release]}
            assert path == "/ws/2/release/" + mbid
            return {**release, "media": [{"tracks": [
                {"title": f"Track {i}", "recording": {"id": str(uuid.UUID(int=i))}}
                for i in range(1, 14)
            ]}]}

    class Source:
        async def search(self, *args, **kwargs):
            raise AssertionError("Exact album must not query YouTube")

    for query in ("Skrillex SOMA", "Skrillex альбом SOMA"):
        answers = []
        async def answer(text, **kwargs):
            answers.append((text, kwargs))
        msg = SimpleNamespace(text=query, chat=SimpleNamespace(id=42), answer=answer)
        asyncio.run(plain_music_search(msg, None, db, Source(),
                                       search_enabled=True, download_enabled=True,
                                       catalog=Catalog()))
        assert len(answers) == 1
        text, kwargs = answers[0]
        assert "Skrillex — SOMA" in text and "13. Track 13" in text
        callbacks = {b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row}
        assert f"albtrack:{str(uuid.UUID(int=13))}" in callbacks
        assert f"album:{mbid}:1" not in callbacks
        assert f"albumplay:{mbid}" not in callbacks
    assert not db.all_tracks()


def test_album_page_preserves_order_on_long_releases(tmp_path):
    import uuid
    from music_bot.catalog import CatalogTrack
    from music_bot.telegram_youtube import album_page
    db = Library(tmp_path / "music.db")
    mbid = str(uuid.UUID(int=100))
    tracks = [CatalogTrack(str(uuid.UUID(int=i)), f"Track {i}", "Artist")
              for i in range(1, 18)]
    first, first_markup = album_page(db, mbid, "Album", "Artist", tracks, 0)
    second, second_markup = album_page(db, mbid, "Album", "Artist", tracks, 1)
    assert "15. Track 15" in first and "16. Track 16" not in first
    assert "16. Track 16" in second and "17. Track 17" in second
    assert f"album:{mbid}:1" in {b.callback_data for row in first_markup.inline_keyboard for b in row}
    assert f"album:{mbid}:0" in {b.callback_data for row in second_markup.inline_keyboard for b in row}


def test_album_catalog_outage_still_allows_song_search(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from music_bot.catalog import CatalogError
    from music_bot.telegram_youtube import plain_music_search
    db = Library(tmp_path / "music.db")

    class Catalog:
        async def _json(self, *args, **kwargs):
            raise CatalogError("offline")

    class Source:
        async def search(self, query, *, limit):
            return [VideoCandidate(ID, "Artist Song", "Artist", 100)]

    answers = []
    async def answer(text, **kwargs):
        answers.append((text, kwargs))
    msg = SimpleNamespace(text="Artist Song", chat=SimpleNamespace(id=42), answer=answer)
    asyncio.run(plain_music_search(msg, None, db, Source(),
                                   search_enabled=True, download_enabled=True,
                                   catalog=Catalog()))
    assert "Artist Song" in answers[0][0]
    assert db.get_youtube_video(ID)


def test_saved_album_collection_persists_without_audio(tmp_path):
    import uuid
    from music_bot.catalog import CatalogTrack
    from music_bot.telegram_youtube import album_page, album_collection_page
    path = tmp_path / "music.db"
    db = Library(path)
    mbid = str(uuid.UUID(int=100))
    tracks = [CatalogTrack(str(uuid.UUID(int=1)), "Song", "Artist")]
    def callbacks(markup):
        return {b.callback_data for row in markup.inline_keyboard for b in row}

    _, markup = album_page(db, mbid, "Album", "Artist", tracks, 0)
    assert f"albumadd:{mbid}:0" in callbacks(markup)
    assert db.save_album(mbid, "Album", "Artist", 1)
    assert not db.save_album(mbid, "Album", "Artist", 1)
    db = Library(path)
    assert len(db.saved_albums()) == 1
    assert not db.all_tracks()
    text, markup = album_collection_page(db)
    assert "Artist — Album (1)" in text
    assert f"album:{mbid}:0" in callbacks(markup)
    _, markup = album_page(db, mbid, "Album", "Artist", tracks, 0)
    assert f"albumdel:{mbid}:0" in callbacks(markup)
    assert f"albumplay:{mbid}" not in callbacks(markup)
    assert db.remove_saved_album(mbid)
    assert not db.remove_saved_album(mbid)
    assert db.saved_albums() == []
    assert "Пока пусто" in album_collection_page(db)[0]


def test_album_collection_pages_and_home_menu(tmp_path):
    import uuid
    from music_bot.telegram_youtube import album_collection_page
    from music_bot.main import home_keyboard
    db = Library(tmp_path / "music.db")
    for i in range(1, 13):
        db.save_album(str(uuid.UUID(int=i)), f"Release {i}", "Artist", 13)
    first, first_markup = album_collection_page(db, 0)
    second, second_markup = album_collection_page(db, 1)
    def callbacks(markup):
        return {b.callback_data for row in markup.inline_keyboard for b in row}
    assert "1. Artist — Release 12" in first
    assert "11. Artist — Release 2" in second
    assert "albumlist:1" in callbacks(first_markup)
    assert "albumlist:0" in callbacks(second_markup)
    assert "albumlist:2" not in callbacks(second_markup)
    assert "nav:albums" in callbacks(home_keyboard())
    assert album_collection_page(db, 100)[0] == first


def test_album_partial_playback_never_uses_unconfirmed_audio(tmp_path):
    import uuid
    from music_bot.catalog import CatalogTrack
    from music_bot.telegram_youtube import album_page, album_track_ids, available_album_ids, saved_album_ids
    db = Library(tmp_path / "music.db")
    release = str(uuid.UUID(int=100))
    tracks = [CatalogTrack(str(uuid.UUID(int=i)), f"Song {i}", "Artist") for i in range(1, 4)]
    db.remember_candidates(tracks)
    # Insert out of album order, and leave the middle track unconfirmed.
    third = db.add_track("f3", "u3", tracks[2].title, "Artist")
    first = db.add_track("f1", "u1", tracks[0].title, "Artist")
    middle = db.add_track("f2", "u2", tracks[1].title, "Artist")
    db.confirm_recording_match(third.id, tracks[2].mbid)
    db.confirm_recording_match(first.id, tracks[0].mbid)
    assert album_track_ids(db, tracks) == [first.id, None, third.id]
    assert available_album_ids(db, tracks) == [first.id, third.id]
    assert saved_album_ids(db, tracks) == []
    text, markup = album_page(db, release, "Album", "Artist", tracks, 0)
    assert "2/3" in text
    callbacks = {b.callback_data for row in markup.inline_keyboard for b in row}
    assert f"albumready:{release}" in callbacks
    assert f"albumnext:{release}" in callbacks
    assert f"albumplay:{release}" not in callbacks
    db.confirm_recording_match(middle.id, tracks[1].mbid)
    assert saved_album_ids(db, tracks) == [first.id, middle.id, third.id]
    _, markup = album_page(db, release, "Album", "Artist", tracks, 0)
    callbacks = {b.callback_data for row in markup.inline_keyboard for b in row}
    assert f"albumplay:{release}" in callbacks
    assert f"albumready:{release}" not in callbacks
    assert f"albumnext:{release}" not in callbacks
