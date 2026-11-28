"""One-time owner-authorized migration of existing Telegram audio into LAIN.

Never polls or sends messages. The token exists only in this operator process.
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from aiogram.exceptions import TelegramRetryAfter

MAX_BYTES=19*1024*1024


def audio_extension(path):
    with path.open("rb") as stream:
        magic=stream.read(16)
    if magic.startswith(b"ID3") or (len(magic)>=2 and magic[0]==255 and magic[1]&224==224):
        return ".mp3"
    if len(magic)>=12 and magic[4:8]==b"ftyp":
        return ".m4a"
    if magic.startswith(b"fLaC"):
        return ".flac"
    if magic.startswith(b"OggS"):
        return ".ogg"
    raise ValueError("Unsupported audio signature")


async def migrate(store, bot, *, after=0, limit=50):
    if not 0<=after<=9223372036854775807 or not 1<=limit<=100:
        raise ValueError("Invalid migration page")
    db=store.library
    with db.con() as con:
        rows=[db.track(row) for row in con.execute(
            """SELECT * FROM tracks WHERE id>? AND file_id NOT LIKE 'native:%'
               ORDER BY id LIMIT ?""",(after,limit+1))]
    results=[]
    last=after
    stopped=False
    for track in rows[:limit]:
        if store.audio(track.id):
            results.append({"id":track.id,"status":"already_ready"})
            last=track.id
            continue
        if shutil.disk_usage(store.root).free<1024**3+2*MAX_BYTES:
            stopped=True
            results.append({"id":track.id,"status":"low_disk"})
            break
        try:
            async with asyncio.timeout(110):
                info=await bot.get_file(track.file_id)
                if type(info.file_size) is not int or not 1<=info.file_size<=MAX_BYTES:
                    results.append({"id":track.id,"status":"size_limit"})
                    last=track.id
                    continue
                if not isinstance(info.file_path,str) or not info.file_path:
                    raise ValueError("Missing file path")
                with tempfile.TemporaryDirectory(prefix="lain-migration-") as directory:
                    path=Path(directory)/"download"
                    await bot.download_file(info.file_path,destination=path,timeout=90)
                    if not path.is_file() or path.stat().st_size!=info.file_size:
                        raise ValueError("Incomplete audio")
                    typed=path.with_suffix(audio_extension(path))
                    path.rename(typed)
                    store.import_file(typed,title=track.title.strip() or "Unknown title",
                                      artist=track.artist.strip() or "Unknown artist",track_id=track.id)
            results.append({"id":track.id,"status":"ready"})
        except TelegramRetryAfter:
            results.append({"id":track.id,"status":"rate_limited"})
            stopped=True
            break
        except Exception:
            # Never include raw Telegram exceptions, token, or remote file path.
            results.append({"id":track.id,"status":"unavailable"})
        last=track.id
    return {"results":results,"next_after":last if stopped or len(rows)>limit else None,
            "stopped":stopped}
