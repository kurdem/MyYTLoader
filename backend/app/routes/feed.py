from __future__ import annotations

from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, Response

from ..deps import require_user, services
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


@private.get("")
def feed_info(request: Request) -> dict:
    svc = services(request)
    token = svc.feed.get()
    base = base_url(request)
    return {
        "base_url": base,
        "https": base.startswith("https://"),
        "media_base": f"{base}/feed/{token}/media",
        "feeds": [
            {"name": "Alle Audiodateien", "url": _feed_url(base, token)},
            {"name": "Alle Videos", "url": _feed_url(base, token, kind="video")},
            *({"name": c["name"], "url": _feed_url(base, token, c["name"])} for c in svc.library.collections()),
        ],
    }


@private.post("/rotate")
def rotate(request: Request) -> dict:
    services(request).feed.rotate()
    return feed_info(request)


@public.get("/{token}/podcast.xml")
def podcast(token: str, request: Request, collection: str | None = None,
            kind: Literal["audio", "video", "all"] = "audio") -> Response:
    svc = services(request)
    svc.feed.verify(token)
    base = base_url(request)
    items = svc.library.list(kind=None if kind == "all" else kind, collection=collection, limit=2000)
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
    svc = services(request)
    svc.feed.verify(token)
    return media_response(svc, media_id, attachment=False)


@public.get("/{token}/cover/{media_id}.jpg")
def feed_cover(token: str, media_id: str, request: Request) -> FileResponse:
    svc = services(request)
    svc.feed.verify(token)
    return FileResponse(svc.library.cover_file(media_id), media_type="image/jpeg")


@public.get("/{token}/artwork.png")
def artwork(token: str, request: Request) -> FileResponse:
    svc = services(request)
    svc.feed.verify(token)
    path = svc.settings.frontend_path / "icons" / "podcast-1400.png"
    if not path.is_file():
        raise UserError("Kein Artwork vorhanden.", 404)
    return FileResponse(path, media_type="image/png")
