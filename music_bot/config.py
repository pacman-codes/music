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
    webapp_url: str | None = None
    allowed_media_hosts: tuple[str, ...] = ()
    youtube_search_enabled: bool = False
    youtube_download_enabled: bool = False
    tmdb_read_token: str | None = None
    acoustid_client_key: str | None = None

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
    url = os.getenv("WEBAPP_URL", "").strip() or None
    if url and (not url.startswith("https://") or len(url) > 200):
        raise RuntimeError("WEBAPP_URL must be a public HTTPS URL")
    hosts = tuple(
        x.strip().lower() for x in os.getenv("MUSIC_ALLOWED_MEDIA_HOSTS", "").split(",")
        if x.strip()
    )
    search = os.getenv("MUSIC_ENABLE_YOUTUBE_SEARCH", "").strip().lower() in ("1", "true", "yes")
    download = os.getenv("MUSIC_ENABLE_YOUTUBE_DOWNLOAD", "").strip().lower() in ("1", "true", "yes")
    if download and not search:
        raise RuntimeError("YouTube downloading requires search to be enabled")
    return Settings(
        token, owner_id, Path(os.getenv("MUSIC_DB_PATH", "data/music.db")), url,
        hosts, search, download,
        os.getenv("TMDB_READ_TOKEN", "").strip() or None,
        os.getenv("ACOUSTID_CLIENT_KEY", "").strip() or None,
    )
