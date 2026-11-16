from __future__ import annotations

import asyncio
import shutil
import subprocess
from functools import lru_cache

from fastapi import APIRouter, Depends, Request

from .. import __version__
from ..db import utcnow
from ..auth import is_admin
from ..deps import AdminUser, CurrentUser, require_user, services
from ..maintenance import cleanup_temp

public = APIRouter(prefix="/api", tags=["system"])
router = APIRouter(prefix="/api/system", tags=["system"], dependencies=[Depends(require_user)])


@lru_cache
def deno_version(deno: str | None) -> str | None:
    if not deno:
        return None
    try:
        out = subprocess.run([deno, "--version"], capture_output=True, text=True, timeout=15)
        return out.stdout.split()[1] if out.returncode == 0 and out.stdout else None
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return None


@public.get("/health")
def health(request: Request) -> dict:
    services(request).db.one("SELECT 1")
    return {"status": "ok"}


@router.get("")
def info(user: CurrentUser, request: Request) -> dict:
    svc = services(request)
    s = svc.settings
    admin = is_admin(user)
    disk: dict = {}
    if admin:
        try:
            usage = shutil.disk_usage(s.download_path)
            disk = {"total": usage.total, "used": usage.used, "free": usage.free}
        except OSError:
            pass
    return {
        "is_admin": admin,
        "versions": {
            "app": __version__,
            "yt_dlp": svc.ytdlp.version(),
            "ffmpeg": svc.ffmpeg.version(),
            "deno": deno_version(svc.ytdlp.deno_path()),
        },
        "ytdlp": {
            "auto_update": s.ytdlp_auto_update,
            "channel": s.ytdlp_channel,
            "last_update": svc.db.kv_get("ytdlp_last_update"),
        },
        "settings": {
            "auth_enabled": s.auth_enabled,
            "max_concurrent_downloads": s.max_concurrent_downloads,
            "max_file_size_mb": s.max_file_size_mb,
            "default_audio_format": s.default_audio_format,
            "default_audio_bitrate": s.default_audio_bitrate,
            "default_normalize": s.default_normalize,
            "default_trim_silence": s.default_trim_silence,
            "keep_original": s.keep_original,
            "allowed_domains": list(s.allowed_domains),
            "playlist_max_items": s.playlist_max_items,
            "retention_days": s.retention_days,
        },
        "library": svc.library.stats(None if admin else user["id"]),
        "disk": disk,
    }


@router.post("/ytdlp/update")
async def update_ytdlp(_: AdminUser, request: Request) -> dict:
    svc = services(request)
    result = await asyncio.to_thread(svc.ytdlp.update)
    svc.db.kv_set("ytdlp_last_update", utcnow())
    return result


@router.post("/cleanup-temp")
def cleanup(_: AdminUser, request: Request) -> dict:
    svc = services(request)
    return {"deleted": cleanup_temp(svc, max_age_hours=0)}
