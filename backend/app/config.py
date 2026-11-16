"""Zentrale Konfiguration aus Umgebungsvariablen."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

AUDIO_FORMATS = ("m4a", "mp3", "opus", "flac")
VIDEO_FORMATS = ("mp4", "webm")
ALL_FORMATS = AUDIO_FORMATS + VIDEO_FORMATS

DEFAULT_ALLOWED_DOMAINS = (
    "youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
)


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "ja")


def _int(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.environ.get(name)
    try:
        value = int(raw) if raw not in (None, "") else default
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    return raw.strip() if raw not in (None, "") else default


def _bitrate(raw: str) -> str:
    raw = raw.lower().strip()
    if not raw.endswith("k"):
        raw += "k"
    num = int(raw[:-1])
    if not 32 <= num <= 512:
        raise ValueError("DEFAULT_AUDIO_BITRATE muss zwischen 32k und 512k liegen")
    return f"{num}k"


@dataclass(frozen=True)
class Settings:
    app_port: int
    download_path: Path
    temp_path: Path
    database_path: Path
    config_path: Path
    frontend_path: Path
    max_concurrent_downloads: int
    max_file_size_mb: int
    default_audio_format: str
    default_audio_bitrate: str
    default_normalize: bool
    default_trim_silence: bool
    keep_original: bool
    auth_enabled: bool
    admin_username: str
    admin_password: str
    admin_password_reset: bool
    default_user_quota_mb: int
    default_user_max_jobs: int
    secret_key: str
    session_max_age_days: int
    base_url: str
    allowed_domains: tuple[str, ...]
    check_dns: bool
    rate_limit_per_minute: int
    temp_max_age_hours: int
    retention_days: int
    playlist_max_items: int
    ytdlp_auto_update: bool
    ytdlp_channel: str
    ytdlp_builtin_venv: Path
    ytdlp_sleep_interval: int
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def ytdlp_user_venv(self) -> Path:
        return self.config_path / "yt-dlp"

    @property
    def ytdlp_cache_dir(self) -> Path:
        return self.config_path / "yt-dlp-cache"

    @property
    def covers_path(self) -> Path:
        return self.download_path / ".covers"

    @property
    def originals_path(self) -> Path:
        return self.download_path / ".originals"


@lru_cache
def get_settings() -> Settings:
    root = Path(__file__).resolve().parents[2]
    fmt = _str("DEFAULT_AUDIO_FORMAT", "m4a").lower()
    if fmt not in AUDIO_FORMATS:
        raise ValueError(f"DEFAULT_AUDIO_FORMAT muss eines von {AUDIO_FORMATS} sein")
    domains = tuple(
        d.strip().lower().lstrip(".")
        for d in _str("ALLOWED_DOMAINS", ",".join(DEFAULT_ALLOWED_DOMAINS)).split(",")
        if d.strip()
    )
    channel = _str("YTDLP_CHANNEL", "stable").lower()
    if channel not in ("stable", "nightly"):
        channel = "stable"
    return Settings(
        app_port=_int("APP_PORT", 8080),
        download_path=Path(_str("DOWNLOAD_PATH", "/downloads")),
        temp_path=Path(_str("TEMP_PATH", "/temp")),
        database_path=Path(_str("DATABASE_PATH", "/database")),
        config_path=Path(_str("CONFIG_PATH", "/config")),
        frontend_path=Path(_str("FRONTEND_PATH", str(root / "frontend"))),
        max_concurrent_downloads=_int("MAX_CONCURRENT_DOWNLOADS", 2, minimum=1),
        max_file_size_mb=_int("MAX_FILE_SIZE_MB", 2048, minimum=1),
        default_audio_format=fmt,
        default_audio_bitrate=_bitrate(_str("DEFAULT_AUDIO_BITRATE", "192k")),
        default_normalize=_bool("DEFAULT_NORMALIZE", True),
        default_trim_silence=_bool("DEFAULT_TRIM_SILENCE", False),
        keep_original=_bool("KEEP_ORIGINAL", False),
        auth_enabled=_bool("AUTH_ENABLED", True),
        admin_username=_str("ADMIN_USERNAME", "admin"),
        admin_password=os.environ.get("ADMIN_PASSWORD", ""),
        admin_password_reset=_bool("ADMIN_PASSWORD_RESET", False),
        default_user_quota_mb=_int("DEFAULT_USER_QUOTA_MB", 0, minimum=0),
        default_user_max_jobs=_int("DEFAULT_USER_MAX_JOBS", 0, minimum=0),
        secret_key=os.environ.get("SECRET_KEY", ""),
        session_max_age_days=_int("SESSION_MAX_AGE_DAYS", 30, minimum=1),
        base_url=_str("BASE_URL", "").rstrip("/"),
        allowed_domains=domains,
        check_dns=_bool("CHECK_DNS", True),
        rate_limit_per_minute=_int("RATE_LIMIT_PER_MINUTE", 30, minimum=1),
        temp_max_age_hours=_int("TEMP_MAX_AGE_HOURS", 12, minimum=1),
        retention_days=_int("RETENTION_DAYS", 0, minimum=0),
        playlist_max_items=_int("PLAYLIST_MAX_ITEMS", 200, minimum=1),
        ytdlp_auto_update=_bool("YTDLP_AUTO_UPDATE", True),
        ytdlp_channel=channel,
        ytdlp_builtin_venv=Path(_str("YTDLP_BUILTIN_VENV", "/opt/yt-dlp")),
        ytdlp_sleep_interval=_int("YTDLP_SLEEP_INTERVAL", 3, minimum=0),
        ffmpeg_bin=_str("FFMPEG_BIN", "ffmpeg"),
        ffprobe_bin=_str("FFPROBE_BIN", "ffprobe"),
    )
