"""Erscheinungsbild: dynamisches index.html/Manifest, Icons, Admin-Einstellungen."""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from ..branding import MAX_ICON_BYTES
from ..deps import AdminUser, services
from ..security import UserError

public = APIRouter(tags=["branding"])
api = APIRouter(prefix="/api/branding", tags=["branding"])


class NameBody(BaseModel):
    name: str | None = Field(default=None, max_length=200)  # None = Standardname


# ------------------------------------------------------------------ öffentlich

@public.get("/", include_in_schema=False)
@public.get("/index.html", include_in_schema=False)
def index(request: Request) -> HTMLResponse:
    svc = services(request)
    template = (svc.settings.frontend_path / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(svc.branding.render_index(template))


@public.get("/manifest.webmanifest", include_in_schema=False)
def manifest(request: Request) -> Response:
    return Response(services(request).branding.manifest(), media_type="application/manifest+json")


@public.get("/branding/{filename}", include_in_schema=False)
def icon(filename: str, request: Request) -> FileResponse:
    path = services(request).branding.file(filename)
    if not path.is_file():
        raise UserError("Nicht gefunden.", status_code=404)
    # URLs tragen ?v=<version> -> lange cachen ist unkritisch
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=604800"})


@api.get("")
def get_branding(request: Request) -> dict:
    return services(request).branding.info()


# ------------------------------------------------------------------ Admin

@api.put("")
def set_name(body: NameBody, _: AdminUser, request: Request) -> dict:
    b = services(request).branding
    return b.reset_name() if body.name is None else b.set_name(body.name)


@api.post("/icon")
async def upload_icon(_: AdminUser, request: Request, file: UploadFile = File(...)) -> dict:
    svc = services(request)
    folder = svc.settings.temp_path / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"icon-{uuid.uuid4().hex}{Path(file.filename or '').suffix.lower()[:6]}"
    size = 0
    try:
        with target.open("wb") as fh:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_ICON_BYTES:
                    raise UserError(f"Das Bild ist größer als {MAX_ICON_BYTES // 1048576} MB.", status_code=413)
                fh.write(chunk)
        return await asyncio.to_thread(svc.branding.set_icon, target)
    finally:
        await file.close()
        target.unlink(missing_ok=True)


@api.delete("/icon")
def reset_icon(_: AdminUser, request: Request) -> dict:
    return services(request).branding.reset_icon()

