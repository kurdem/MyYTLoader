from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..auth import is_admin
from ..deps import CurrentUser, require_user, services
from ..media import MIME_TYPES

router = APIRouter(prefix="/api/media", tags=["library"], dependencies=[Depends(require_user)])


class MediaUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    artist: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=10000)
    collection: str | None = Field(default=None, max_length=300)


class CleanupBody(BaseModel):
    older_than_days: int = Field(ge=0, le=36500)
    all_users: bool = False


def scope(user: dict, requested: str | None) -> str | None:
    """Welche Bibliothek wird angezeigt? Benutzer: immer die eigene. Admins: eigene, alle oder eine bestimmte."""
    if not is_admin(user) or not requested or requested == "me":
        return user["id"]
    return None if requested == "all" else requested


@router.get("")
def list_media(
    request: Request,
    user: CurrentUser,
    q: str | None = None,
    kind: Literal["audio", "video", "all"] = "all",
    collection: str | None = None,
    sort: Literal["created", "title", "artist", "size", "duration"] = "created",
    order: Literal["asc", "desc"] = "desc",
    owner: str | None = None,
    limit: int = 1000,
    offset: int = 0,
) -> list[dict]:
    return services(request).library.list(scope(user, owner), q, None if kind == "all" else kind, collection,
                                          sort, order, limit, offset)


@router.get("/collections")
def collections(request: Request, user: CurrentUser, owner: str | None = None) -> list[dict]:
    return services(request).library.collections(scope(user, owner))


@router.post("/cleanup")
def cleanup(body: CleanupBody, user: CurrentUser, request: Request) -> dict:
    target = None if body.all_users and is_admin(user) else user["id"]
    return {"deleted": services(request).library.delete_older_than(body.older_than_days, target)}


@router.get("/{media_id}")
def get_media(media_id: str, user: CurrentUser, request: Request) -> dict:
    return services(request).library.get(media_id, user)


@router.patch("/{media_id}")
def update_media(media_id: str, body: MediaUpdate, user: CurrentUser, request: Request) -> dict:
    return services(request).library.update(media_id, body.model_dump(exclude_unset=True), user)


@router.delete("/{media_id}")
def delete_media(media_id: str, user: CurrentUser, request: Request) -> dict:
    services(request).library.delete(media_id, user)
    return {"ok": True}


def media_response(svc, media_id: str, attachment: bool, user: dict | None) -> FileResponse:
    row = svc.library.get(media_id, user)
    path = svc.library.file_path(media_id, user)
    return FileResponse(
        path,
        media_type=MIME_TYPES.get(row["format"], "application/octet-stream"),
        filename=row["filename"],
        content_disposition_type="attachment" if attachment else "inline",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/{media_id}/file")
def stream_media(media_id: str, user: CurrentUser, request: Request) -> FileResponse:
    """Inline (Range-fähig) für den Player im Browser."""
    return media_response(services(request), media_id, False, user)


@router.get("/{media_id}/download")
def download_media(media_id: str, user: CurrentUser, request: Request) -> FileResponse:
    """Als Anhang – Safari bietet dann „In Dateien sichern“ bzw. den Download-Manager an."""
    return media_response(services(request), media_id, True, user)


@router.get("/{media_id}/cover")
def cover(media_id: str, user: CurrentUser, request: Request) -> FileResponse:
    path = services(request).library.cover_file(media_id, user)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
