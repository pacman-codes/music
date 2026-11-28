import asyncio
from collections import namedtuple
from types import SimpleNamespace

from music_bot.native_migrate_audio import migrate, MAX_BYTES
from music_bot.native_storage import NativeStorage
from music_bot.storage import Library


def test_transfer_keeps_original_track_id_and_resume_cursor(tmp_path,monkeypatch):
    db=Library(tmp_path/"music.db")
    store=NativeStorage(db,tmp_path/"audio")
    a=db.add_track("audio1","u1","Song","Artist")
    b=db.add_track("audio2","u2","Big","Band")
    c=db.add_track("audio3","u3","Broken","Band")
    db.set_rating(a.id,10)
    db.like(a.id)
    data=b"ID3"+b"x"*100
    class Bot:
        def __init__(self):self.requested=[]
        async def get_file(self,ident):
            self.requested.append(ident)
            return SimpleNamespace(file_path=ident,file_size=MAX_BYTES+1 if ident=="audio2" else len(data))
        async def download_file(self,ident,destination,timeout):
            destination.write_bytes(data if ident=="audio1" else b"broken")
    usage=namedtuple("Usage","total used free")
    monkeypatch.setattr("music_bot.native_migrate_audio.shutil.disk_usage",lambda _:usage(10**10,0,10**10))
    bot=Bot()
    result=asyncio.run(migrate(store,bot,limit=2))
    assert result=={"results":[{"id":a.id,"status":"ready"},{"id":b.id,"status":"size_limit"}],"next_after":b.id,"stopped":False}
    assert store.audio(a.id)[0].read_bytes()==data
    assert a.id in db.liked_ids() and db.ratings()[a.id]==10
    assert len(db.all_tracks())==3
    assert asyncio.run(migrate(store,bot,after=b.id))["results"]==[{"id":c.id,"status":"unavailable"}]
    bot.requested.clear()
    assert asyncio.run(migrate(store,bot,limit=1))["results"]==[{"id":a.id,"status":"already_ready"}]
    assert not bot.requested
    assert not db.playback_stats()
