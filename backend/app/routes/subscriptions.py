"""Automatische Abos (Kanäle/Playlists)."""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..config import ALL_FORMATS
from ..deps import CurrentUser, require_user, services
from ..security import UserError
from .jobs import JobOptions, _options

router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"], dependencies=[Depends(require_user)])


class CreateSubscription(BaseModel):
    url: str = Field(max_length=2048)
    format: str = "m4a"
    options: JobOptions = JobOptions()
    interval_hours: int = Field(default=6, ge=1, le=168)
    initial: Literal["none", "latest", "all"] = "none"
    initial_count: int = Field(default=3, ge=0, le=500)
    collection: str | None = Field(default=None, max_length=300)
    feed_mode: Literal["none", "new", "existing"] = "none"
    feed_id: str | None = Field(default=None, max_length=64)
    feed_type: Literal["episodic", "serial"] | None = None


class UpdateSubscription(BaseModel):
    enabled: bool | None = None
    interval_hours: int | None = Field(default=None, ge=1, le=168)
    format: str | None = None
    options: JobOptions | None = None
    collection: str | None = Field(default=None, max_length=300)
    feed_id: str | None = Field(default=None, max_length=64)


@router.get("")
def list_subscriptions(user: CurrentUser, request: Request) -> list[dict]:
    return services(request).subscriptions.list(user)


@router.post("")
async def create_subscription(body: CreateSubscription, user: CurrentUser, request: Request) -> dict:
    svc = services(request)
    svc.api_limiter.check(user["id"])
    fmt = body.format.lower()
    if fmt not in ALL_FORMATS:
        raise UserError("Unbekanntes Ausgabeformat.")
    opts = _options(svc, fmt, body.options)
    return await asyncio.to_thread(
        svc.subscriptions.create, user, url=body.url, fmt=fmt, options=opts,
        interval_hours=body.interval_hours, initial=body.initial, initial_count=body.initial_count,
        collection=body.collection, feed_mode=body.feed_mode, feed_id=body.feed_id, feed_type=body.feed_type,
    )


@router.patch("/{sub_id}")
def update_subscription(sub_id: str, body: UpdateSubscription, user: CurrentUser, request: Request) -> dict:
    svc = services(request)
    changes = body.model_dump(exclude_unset=True)
    if "options" in changes and body.options is not None:
        fmt = (body.format or svc.subscriptions.get(sub_id, user)["format"]).lower()
        changes["options"] = _options(svc, fmt, body.options)
    return svc.subscriptions.update(sub_id, user, changes)


@router.delete("/{sub_id}")
def delete_subscription(sub_id: str, user: CurrentUser, request: Request) -> dict:
    services(request).subscriptions.delete(sub_id, user)
    return {"ok": True}


@router.post("/{sub_id}/check")
async def check_now(sub_id: str, user: CurrentUser, request: Request) -> dict:
    svc = services(request)
    svc.api_limiter.check(user["id"])
    return await asyncio.to_thread(svc.subscriptions.check, sub_id, user)
