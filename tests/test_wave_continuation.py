"""Open-ended Wave batches: re-score only when user requests more music."""
import asyncio

from music_bot.main import deliver_queue
from music_bot.selection import select_wave_batch
from music_bot.storage import Library


class FakeBot:
    def __init__(self):
        self.groups = []
        self.audio = []
        self.messages = []

    async def send_audio(self, *args, **kwargs):
        self.audio.append((args, kwargs))

    async def send_media_group(self, *args, **kwargs):
        self.groups.append((args, kwargs))

    async def send_message(self, *args, **kwargs):
        self.messages.append((args, kwargs))


def fill(db, count):
    return [
        db.add_track(f"file{i}", f"unique{i}", f"Track {i}", f"Artist {i%5}")
        for i in range(count)
    ]


def test_wave_recomputes_next_batch_from_new_feedback(tmp_path):
    db = Library(tmp_path / "music.db")
    fill(db, 28)
    initial = select_wave_batch(db, "balanced")
    assert len(initial) == 10
    q = db.create_queue([t.id for t in initial], "Моя волна", "balanced")
    bot = FakeBot()

    async def exercise():
        await deliver_queue(bot, db, 101, q, asyncio.Lock())
        first_card = bot.messages[-1][1]["reply_markup"]
        assert any(
            b.callback_data == f"more:{q}:10"
            for row in first_card.inline_keyboard for b in row
        )
        disliked_by_skips = initial[0].id
        for _ in range(3):
            db.record_playback_event(disliked_by_skips, "skip")
        # The next "Ещё 10" is freshly ranked, not the old snapshot.
        assert db.peek_queue(q).tracks == ()
        await deliver_queue(bot, db, 101, q, asyncio.Lock())
        assert len(bot.groups) == 2
        assert disliked_by_skips not in {t.id for t in db.peek_queue(q).tracks}
        # Batch has already advanced to cursor 20. Inspect persisted positions.
        with db.con() as con:
            last = [r[0] for r in con.execute(
                "SELECT track_id FROM queue_items WHERE queue_id=? AND position>=10",
                (q,),
            )]
        assert disliked_by_skips not in last
        assert len(last) == 10
        assert db.queue_kind(q) == "balanced"
        # Last 3 sent tracks are de-prioritized by hard exclusion where feasible.
        next_wave = select_wave_batch(db, "balanced")
        assert not set(next_wave[:3]).intersection(
            {t for t in list(db.latest_sent_ids(3)) if t == disliked_by_skips}
        )

    asyncio.run(exercise())


def test_static_playlist_does_not_regenerate(tmp_path):
    db = Library(tmp_path / "music.db")
    songs = fill(db, 4)
    playlist = db.create_playlist("Road")
    q = db.create_queue([t.id for t in songs], "Road", "playlist")
    bot = FakeBot()

    async def exercise():
        await deliver_queue(bot, db, 101, q, asyncio.Lock())
        assert not db.extend_exhausted_queue(q, [songs[0].id])
        await deliver_queue(bot, db, 101, q, asyncio.Lock())
        assert len(bot.groups) == 1
        assert len(bot.messages) == 2

    asyncio.run(exercise())


def test_append_requires_exhaustion_and_is_atomic(tmp_path):
    db = Library(tmp_path / "music.db")
    tracks = fill(db, 15)
    q = db.create_queue([t.id for t in tracks[:10]], "Radio", "discover")
    assert not db.extend_exhausted_queue(q, [t.id for t in tracks[10:]])
    assert db.mark_queue_sent(db.peek_queue(q))
    assert db.extend_exhausted_queue(q, [t.id for t in tracks[10:]])
    assert not db.extend_exhausted_queue(q, [tracks[0].id])
    assert len(db.peek_queue(q).tracks) == 5


def test_tiny_library_falls_back_to_replay(tmp_path):
    db = Library(tmp_path / "music.db")
    only = fill(db, 1)[0]
    assert [t.id for t in select_wave_batch(db, "balanced")] == [only.id]
    db.record_playback_event(only.id, "complete")
    q = db.create_queue([only.id], "Radio", "balanced")
    db.mark_queue_sent(db.peek_queue(q))
    assert [t.id for t in select_wave_batch(db, "balanced")] == [only.id]
