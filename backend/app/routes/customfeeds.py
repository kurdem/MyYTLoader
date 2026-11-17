"""Eigene Feeds verwalten (immer nur die eigenen)."""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..deps import CurrentUser, require_user, services
from ..security import UserError

router = APIRouter(prefix="/api/feeds", tags=["feeds"], dependencies=[Depends(require_user)])
MAX_COVER_BYTES = 10 * 1024 * 1024


class CreateFeed(BaseModel):
    title: str = Field(max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    type: Literal["episodic", "serial"] = "episodic"
    collection: str | None = Field(default=None, max_length=300)  # "Aus Ordner erstellen"
    media_ids: list[str] | None = Field(default=None, max_length=5000)


class UpdateFeed(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    type: Literal["episodic", "serial"] | None = None


class Items(BaseModel):
    media_ids: list[str] = Field(max_length=5000)


class SortBody(BaseModel):
    by: Literal["track", "title", "created", "published"]


def _with_urls(request: Request, user: dict, feed: dict) -> dict:
    from .feed import base_url

    feed["url"] = f"{base_url(request)}/feed/{user['feed_token']}/f/{feed['id']}.xml"
    return feed


@router.get("")
def list_feeds(user: CurrentUser, request: Request) -> list[dict]:
    return [_with_urls(request, user, f) for f in services(request).feeds.list(user["id"])]


@router.post("")
def create_feed(body: CreateFeed, user: CurrentUser, request: Request) -> dict:
    feed = services(request).feeds.create(user, body.title, body.description, body.type,
                                          body.collection, body.media_ids)
    return _with_urls(request, user, feed)


@router.get("/of-media/{media_id}")
def feeds_of_media(media_id: str, user: CurrentUser, request: Request) -> list[str]:
    return services(request).feeds.feeds_of_media(media_id, user["id"])


@router.get("/{feed_id}")
def get_feed(feed_id: str, user: CurrentUser, request: Request) -> dict:
    return _with_urls(request, user, services(request).feeds.detail(feed_id, user))


@router.patch("/{feed_id}")
def update_feed(feed_id: str, body: UpdateFeed, user: CurrentUser, request: Request) -> dict:
    feed = services(request).feeds.update(feed_id, user, body.model_dump(exclude_unset=True))
    return _with_urls(request, user, feed)


@router.delete("/{feed_id}")
def delete_feed(feed_id: str, user: CurrentUser, request: Request) -> dict:
    services(request).feeds.delete(feed_id, user)
    return {"ok": True}


@router.post("/{feed_id}/items")
def add_items(feed_id: str, body: Items, user: CurrentUser, request: Request) -> dict:
    return _with_urls(request, user, services(request).feeds.add(feed_id, user, body.media_ids))


@router.put("/{feed_id}/items")
def set_items(feed_id: str, body: Items, user: CurrentUser, request: Request) -> dict:
    return _with_urls(request, user, services(request).feeds.set_items(feed_id, user, body.media_ids))


@router.delete("/{feed_id}/items/{media_id}")
def remove_item(feed_id: str, media_id: str, user: CurrentUser, request: Request) -> dict:
    return _with_urls(request, user, services(request).feeds.remove(feed_id, user, media_id))


@router.post("/{feed_id}/sort")
def sort_items(feed_id: str, body: SortBody, user: CurrentUser, request: Request) -> dict:
    return _with_urls(request, user, services(request).feeds.sort(feed_id, user, body.by))


@router.get("/{feed_id}/cover")
def get_cover(feed_id: str, user: CurrentUser, request: Request) -> FileResponse:
    path = services(request).feeds.cover_file(feed_id, user)
    return FileResponse(path, headers={"Cache-Control": "private, max-age=60"})


@router.post("/{feed_id}/cover")
async def upload_cover(feed_id: str, user: CurrentUser, request: Request, file: UploadFile = File(...)) -> dict:
    svc = services(request)
    svc.feeds.get(feed_id, user)  # Besitz prüfen, bevor hochgeladen wird
    folder = svc.settings.temp_path / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"cover-{uuid.uuid4().hex}{Path(file.filename or '').suffix.lower()[:6]}"
    size = 0
    try:
        with target.open("wb") as fh:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_COVER_BYTES:
                    raise UserError("Das Bild ist größer als 10 MB.", status_code=413)
                fh.write(chunk)
        feed = await asyncio.to_thread(svc.feeds.set_cover, feed_id, user, target)
        return _with_urls(request, user, feed)
    finally:
        await file.close()
        target.unlink(missing_ok=True)


@router.delete("/{feed_id}/cover")
def reset_cover(feed_id: str, user: CurrentUser, request: Request) -> dict:
    return _with_urls(request, user, services(request).feeds.reset_cover(feed_id, user))
