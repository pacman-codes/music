"""Local administrator commands for standalone LAIN.

Never expose these commands through a public HTTP endpoint.
"""
import argparse
import os
import sqlite3
from pathlib import Path

from music_bot.native_storage import NativeStorage
from music_bot.storage import Library


def clone_library(source: Path, destination: Path):
    """SQLite online backup; never overwrite a populated standalone DB."""
    if not source.is_file() or source.resolve()==destination.resolve():
        raise ValueError("Invalid source database")
    destination.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(destination,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.close(fd)
    try:
        from contextlib import closing
        with closing(sqlite3.connect(source.resolve().as_uri()+"?mode=ro",uri=True)) as src:
            with closing(sqlite3.connect(destination)) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA quick_check").fetchone()[0]!="ok":
                    raise ValueError("Backup integrity check failed")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def main():
    parser=argparse.ArgumentParser(description="LAIN native administrator")
    parser.add_argument("--root",type=Path,default=Path(os.environ.get("LAIN_DATA_DIR","data/standalone")))
    commands=parser.add_subparsers(dest="command",required=True)
    clone=commands.add_parser("clone")
    clone.add_argument("source",type=Path)
    commands.add_parser("pair")
    commands.add_parser("devices")
    revoke=commands.add_parser("revoke")
    revoke.add_argument("id")
    approve=commands.add_parser("approve-source")
    approve.add_argument("track_id",type=int)
    approve.add_argument("url")
    add=commands.add_parser("import")
    add.add_argument("file",type=Path)
    add.add_argument("--title",required=True)
    add.add_argument("--artist",required=True)
    add.add_argument("--track-id",type=int)
    args=parser.parse_args()
    if args.command=="clone":
        clone_library(args.source,args.root/"music.db")
        print("Copied library. Audio has not been migrated.")
        return
    store=NativeStorage(Library(args.root/"music.db"),args.root/"audio")
    if args.command=="pair":
        print(store.issue_pairing())
    elif args.command=="devices":
        import json
        print(json.dumps(store.devices(),ensure_ascii=False))
    elif args.command=="revoke":
        print("Revoked" if store.revoke(args.id) else "Not found")
    elif args.command=="approve-source":
        from music_bot.native_prefetch import Prefetch, configured_hosts
        Prefetch(store,configured_hosts()).approve(args.track_id,args.url)
        print("Source approved")
    elif args.command=="import":
        print("Track ID:",store.import_file(args.file,title=args.title,artist=args.artist,track_id=args.track_id))


if __name__=="__main__":
    main()
