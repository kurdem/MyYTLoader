from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..config import ALL_FORMATS, AUDIO_FORMATS
from ..deps import CurrentUser, require_user, services
from ..security import UserError, sanitize_filename, validate_url, youtube_video_url

router = APIRouter(prefix="/api", tags=["jobs"], dependencies=[Depends(require_user)])

BITRATES = ("96k", "128k", "160k", "192k", "256k", "320k")
HEIGHTS = (2160, 1440, 1080, 720, 480, 360)


class AnalyzeBody(BaseModel):
    url: str = Field(max_length=2048)


class JobOptions(BaseModel):
    bitrate: str | None = None
    normalize: bool | None = None
    trim_silence: bool | None = None
    keep_original: bool | None = None
    max_height: int | None = None


class Entry(BaseModel):
    id: str = Field(max_length=32)
    title: str | None = Field(default=None, max_length=500)
    duration: float | None = None
    thumbnail: str | None = Field(default=None, max_length=2048)
    channel: str | None = Field(default=None, max_length=300)


class JobBody(BaseModel):
    url: str = Field(max_length=2048)
    format: str
    options: JobOptions = JobOptions()
    title: str | None = Field(default=None, max_length=500)
    channel: str | None = Field(default=None, max_length=300)
    thumbnail: str | None = Field(default=None, max_length=2048)
    duration: float | None = None
    collection: str | None = Field(default=None, max_length=300)
    entries: list[Entry] | None = None


def _options(svc, fmt: str, o: JobOptions) -> dict:
    s = svc.settings
    if o.bitrate is not None and o.bitrate not in BITRATES:
        raise UserError("Ungültige Bitrate.")
    if o.max_height is not None and o.max_height not in HEIGHTS:
        raise UserError("Ungültige Videoauflösung.")
    return {
        "bitrate": o.bitrate or s.default_audio_bitrate,
        "normalize": s.default_normalize if o.normalize is None else o.normalize,
        "trim_silence": s.default_trim_silence if o.trim_silence is None else o.trim_silence,
        "keep_original": s.keep_original if o.keep_original is None else o.keep_original,
        "max_height": o.max_height if fmt not in AUDIO_FORMATS else None,
    }


def _thumb(url: str | None) -> str | None:
    return url if url and url.startswith("https://") else None


@router.post("/analyze")
async def analyze(body: AnalyzeBody, user: CurrentUser, request: Request) -> dict:
    svc = services(request)
    svc.api_limiter.check(user["id"])
    url = validate_url(body.url, svc.settings.allowed_domains, svc.settings.check_dns)
    return await asyncio.to_thread(svc.ytdlp.analyze, url, svc.settings.playlist_max_items)


@router.get("/jobs")
def list_jobs(user: CurrentUser, request: Request,
              status: Literal["active", "done", "failed", "all"] = "all") -> list[dict]:
    return services(request).jobs.list(user, None if status == "all" else status)


@router.post("/jobs")
def create_jobs(body: JobBody, user: CurrentUser, request: Request) -> dict:
    svc = services(request)
    svc.api_limiter.check(user["id"])
    fmt = body.format.lower()
    if fmt not in ALL_FORMATS:
        raise UserError("Unbekanntes Ausgabeformat.")
    opts = _options(svc, fmt, body.options)
    collection = sanitize_filename(body.collection, fallback="") or None if body.collection else None
    created = []
    if body.entries:
        if len(body.entries) > svc.settings.playlist_max_items:
            raise UserError(f"Maximal {svc.settings.playlist_max_items} Einträge pro Playlist.")
        validate_url(body.url, svc.settings.allowed_domains, svc.settings.check_dns)
        for e in body.entries:
            created.append(svc.jobs.create(
                user=user, url=youtube_video_url(e.id), fmt=fmt, options=opts, title=e.title, channel=e.channel,
                thumbnail=_thumb(e.thumbnail), duration=e.duration, collection=collection or "Playlist",
            ))
    else:
        url = validate_url(body.url, svc.settings.allowed_domains, svc.settings.check_dns)
        created.append(svc.jobs.create(
            user=user, url=url, fmt=fmt, options=opts, title=body.title, channel=body.channel,
            thumbnail=_thumb(body.thumbnail), duration=body.duration, collection=collection,
        ))
    return {"jobs": created}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, user: CurrentUser, request: Request) -> dict:
    return services(request).jobs.cancel(job_id, user)


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str, user: CurrentUser, request: Request) -> dict:
    return services(request).jobs.retry(job_id, user)


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str, user: CurrentUser, request: Request) -> dict:
    services(request).jobs.delete(job_id, user)
    return {"ok": True}


@router.delete("/jobs")
def clear_jobs(user: CurrentUser, request: Request) -> dict:
    return {"deleted": services(request).jobs.clear_finished(user)}


@router.post("/uploads")
async def upload(
    request: Request,
    user: CurrentUser,
    file: UploadFile = File(...),
    format: str = Form("m4a"),
    title: str | None = Form(None),
    artist: str | None = Form(None),
    description: str | None = Form(None),
    collection: str | None = Form(None),
    bitrate: str | None = Form(None),
    normalize: bool | None = Form(None),
    trim_silence: bool | None = Form(None),
) -> dict:
    """Eigene Audio-/Videodateien hinzufügen (z. B. für den privaten Podcast-Feed)."""
    svc = services(request)
    svc.api_limiter.check(user["id"])
    remaining = svc.users.remaining_bytes(user)
    if remaining == 0:
        raise UserError("Dein Speicherkontingent ist erschöpft.", status_code=413)
    fmt = format.lower()
    if fmt not in ALL_FORMATS:
        raise UserError("Unbekanntes Ausgabeformat.")
    original = Path(file.filename or "upload").name
    suffix = Path(original).suffix.lower()[:10]
    target_dir = svc.settings.temp_path / "uploads"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{uuid.uuid4().hex}{suffix}"
    size = 0
    limit = svc.settings.max_file_size_bytes if remaining is None else min(svc.settings.max_file_size_bytes, remaining)
    try:
        with target.open("wb") as fh:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise UserError(f"Die Datei ist größer als {limit // 1048576} MB "
                                    "(Dateigrößen-Limit bzw. freies Kontingent).", 413)
                fh.write(chunk)
        probe = await asyncio.to_thread(svc.ffmpeg.probe, target)
        if fmt in AUDIO_FORMATS and not svc.ffmpeg.stream(probe, "audio"):
            raise UserError("Die Datei enthält keine Audiospur.")
        if fmt not in AUDIO_FORMATS and not svc.ffmpeg.stream(probe, "video"):
            raise UserError("Die Datei enthält keine Videospur.")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    opts = _options(svc, fmt, JobOptions(bitrate=bitrate, normalize=normalize, trim_silence=trim_silence,
                                         keep_original=False))
    opts.update({
        "upload_path": str(target),
        "original_name": original,
        "title": (title or "").strip()[:300] or None,
        "artist": (artist or "").strip()[:200] or None,
        "description": (description or "").strip()[:5000] or None,
    })
    col = sanitize_filename(collection, fallback="") or None if collection else None
    job = svc.jobs.create(user=user, url=f"upload:{original}", kind="upload", fmt=fmt, options=opts,
                          title=opts["title"] or Path(original).stem, channel=opts["artist"],
                          duration=svc.ffmpeg.duration(probe), collection=col)
    return {"jobs": [job]}


@router.get("/events")
async def events(user: CurrentUser, request: Request) -> StreamingResponse:
    """Server-Sent Events für Live-Fortschritt (nur eigene Ereignisse, Admins alle)."""
    svc = services(request)
    queue = svc.bus.subscribe(user)

    async def stream():
        try:
            yield "retry: 3000\nevent: hello\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            svc.bus.unsubscribe(queue)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",  # Nginx (Proxy Manager): Puffern deaktivieren
        "Connection": "keep-alive",
    })

