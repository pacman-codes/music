"""Offline readiness checks never contact Telegram or expose credentials."""
import json
import sys

from music_bot.config import Settings
from music_bot.doctor import main, preflight
from music_bot.storage import Library


def test_preflight_does_not_create_or_modify_database(tmp_path):
    path = tmp_path / "new" / "music.sqlite"
    settings = Settings("123:super-secret-token", 42, path)
    checks = {item.name: item for item in preflight(settings)}
    assert checks["database"].status == "warning"
    assert checks["youtube_search"].status == "ok"
    assert not path.exists()  # no auto-migration or file creation
    Library(path).add_track("telegram-file", "telegram-unique", "Song", "Artist")
    next_checks = {item.name: item for item in preflight(settings)}
    assert next_checks["database"].status == "ok"
    assert next_checks["database_integrity"].status == "ok"
    assert "tracks: 1" in next_checks["database_integrity"].message
    assert "super-secret" not in repr(next_checks)


def test_missing_optional_tools_report_errors_only_when_enabled(tmp_path, monkeypatch):
    from music_bot import doctor
    monkeypatch.setattr(doctor.shutil, "which", lambda tool: None)
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda mod: None)
    enabled = Settings(
        "123:secret", 42, tmp_path / "music.sqlite", None, (), True, True
    )
    checks = {item.name: item for item in preflight(enabled)}
    assert checks["yt_dlp"].status == "error"
    assert checks["ffmpeg"].status == "error"
    assert checks["ffprobe"].status == "error"

    disabled = Settings("123:secret", 42, tmp_path / "music.sqlite")
    checks = {item.name: item for item in preflight(disabled)}
    assert "yt_dlp" not in checks
    assert "ffmpeg" not in checks


def test_json_cli_never_prints_bot_or_tmdb_secrets(tmp_path, monkeypatch, capsys):
    from music_bot import doctor
    settings = Settings(
        "123:DO-NOT-PRINT-SECRET", 42, tmp_path / "music.sqlite",
        "https://example.org/private-path", (), False, False,
        "TMDB-SECRET-NEVER-PRINT",
    )
    monkeypatch.setattr(doctor, "load_settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["music-doctor", "--json"])
    main()
    output = capsys.readouterr().out
    checks = json.loads(output)
    assert isinstance(checks, list)
    assert all("status" in check for check in checks)
    assert "DO-NOT-PRINT-SECRET" not in output
    assert "TMDB-SECRET" not in output
    assert "private-path" not in output
