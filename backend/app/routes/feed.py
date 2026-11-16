from __future__ import annotations

from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, Response

from ..deps import CurrentUser, require_user, services
from ..feed import build_feed
from ..security import UserError
from .library import media_response

public = APIRouter(prefix="/feed", tags=["feed"])
private = APIRouter(prefix="/api/feed", tags=["feed"], dependencies=[Depends(require_user)])


def base_url(request: Request) -> str:
    configured = services(request).settings.base_url
    return configured or str(request.base_url).rstrip("/")


def _feed_url(base: str, token: str, collection: str | None = None, kind: str = "audio") -> str:
    url = f"{base}/feed/{token}/podcast.xml"
    params = []
    if collection:
        params.append("collection=" + quote(collection))
    if kind != "audio":
        params.append("kind=" + kind)
    return url + ("?" + "&".join(params) if params else "")


def _feed_owner(request: Request, token: str) -> dict:
    user = services(request).users.by_feed_token(token)
    if not user:
        raise UserError("Unbekannter Feed.", status_code=404)
    return user


def feed_info(request: Request, user: dict) -> dict:
    svc = services(request)
    token = user["feed_token"]
    base = base_url(request)
    return {
        "base_url": base,
        "https": base.startswith("https://"),
        "media_base": f"{base}/feed/{token}/media",
        "feeds": [
            {"name": "Alle Audiodateien", "url": _feed_url(base, token)},
            {"name": "Alle Videos", "url": _feed_url(base, token, kind="video")},
            *({"name": c["name"], "url": _feed_url(base, token, c["name"])}
              for c in svc.library.collections(user["id"])),
        ],
    }


@private.get("")
def get_feed(user: CurrentUser, request: Request) -> dict:
    return feed_info(request, user)


@private.post("/rotate")
def rotate(user: CurrentUser, request: Request) -> dict:
    svc = services(request)
    svc.users.rotate_feed_token(user["id"])
    return feed_info(request, svc.users.require(user["id"]))


@public.get("/{token}/podcast.xml")
def podcast(token: str, request: Request, collection: str | None = None,
            kind: Literal["audio", "video", "all"] = "audio") -> Response:
    svc = services(request)
    owner = _feed_owner(request, token)
    base = base_url(request)
    items = svc.library.list(owner["id"], kind=None if kind == "all" else kind, collection=collection, limit=2000)
    title = collection or ("MyYTLoader – Videos" if kind == "video" else "MyYTLoader")
    image = f"{base}/feed/{token}/artwork.png"
    if collection and items and items[0]["has_cover"]:
        image = f"{base}/feed/{token}/cover/{items[0]['id']}.jpg"
    xml = build_feed(
        items, base, token, title=title,
        description="Privater Feed deiner selbst gehosteten Medienbibliothek.",
        self_url=str(request.url), image_url=image,
    )
    return Response(xml, media_type="application/rss+xml; charset=utf-8",
                    headers={"Cache-Control": "no-cache", "X-Robots-Tag": "noindex, nofollow"})


@public.api_route("/{token}/media/{media_id}/{filename}", methods=["GET", "HEAD"])
def feed_media(token: str, media_id: str, filename: str, request: Request) -> FileResponse:
    owner = _feed_owner(request, token)
    # owner wird wie ein normaler Benutzer geprüft: nur eigene Medien über den eigenen Token
    return media_response(services(request), media_id, False, {**owner, "role": "user"})


@public.get("/{token}/cover/{media_id}.jpg")
def feed_cover(token: str, media_id: str, request: Request) -> FileResponse:
    owner = _feed_owner(request, token)
    path = services(request).library.cover_file(media_id, {**owner, "role": "user"})
    return FileResponse(path, media_type="image/jpeg")


@public.get("/{token}/artwork.png")
def artwork(token: str, request: Request) -> FileResponse:
    _feed_owner(request, token)
    path = services(request).settings.frontend_path / "icons" / "podcast-1400.png"
    if not path.is_file():
        raise UserError("Kein Artwork vorhanden.", 404)
    return FileResponse(path, media_type="image/png")
