from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

@dataclass(frozen=True)
class Settings:
    token: str
    owner_id: int
    db_path: Path

def load_settings() -> Settings:
    load_dotenv()
    token = os.getenv("BOT_TOKEN", "").strip()
    owner = os.getenv("OWNER_TELEGRAM_ID", "").strip()
    if not token or not owner:
        raise RuntimeError("BOT_TOKEN and OWNER_TELEGRAM_ID are required")
    try:
        owner_id = int(owner)
    except ValueError as exc:
        raise RuntimeError("OWNER_TELEGRAM_ID must be an integer") from exc
    if owner_id <= 0:
        raise RuntimeError("OWNER_TELEGRAM_ID must be positive")
    return Settings(token, owner_id, Path(os.getenv("MUSIC_DB_PATH", "data/music.db")))
