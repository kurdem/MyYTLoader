"""Privater Podcast-RSS-Feed (Token in der URL, da Podcast-Apps keine Cookies nutzen)."""
from __future__ import annotations

import hmac
import secrets
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import quote
from xml.sax.saxutils import escape

from .db import Database
from .media import MIME_TYPES
from .security import UserError

FEED_TOKEN_KEY = "feed_token"


class FeedTokens:
    def __init__(self, db: Database):
        self.db = db

    def get(self) -> str:
        token = self.db.kv_get(FEED_TOKEN_KEY)
        if not token:
            token = self.rotate()
        return token

    def rotate(self) -> str:
        token = secrets.token_urlsafe(24)
        self.db.kv_set(FEED_TOKEN_KEY, token)
        return token

    def verify(self, token: str) -> None:
        if not hmac.compare_digest(token.encode(), self.get().encode()):
            raise UserError("Unbekannter Feed.", status_code=404)


def _rfc822(iso: str | None) -> str:
    try:
        dt = datetime.fromisoformat(iso) if iso else datetime.now(timezone.utc)
    except ValueError:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return format_datetime(dt)


def _duration(seconds: float | None) -> str:
    s = int(seconds or 0)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def build_feed(items: list[dict], base: str, token: str, title: str, description: str,
               self_url: str, image_url: str) -> str:
    """Erzeugt RSS 2.0 mit iTunes-Namespace. itunes:block verhindert eine Aufnahme ins Apple-Verzeichnis."""
    e = escape
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
        'xmlns:atom="http://www.w3.org/2005/Atom" xmlns:content="http://purl.org/rss/1.0/modules/content/">',
        "<channel>",
        f"<title>{e(title)}</title>",
        f"<link>{e(base)}/</link>",
        f'<atom:link href="{e(self_url)}" rel="self" type="application/rss+xml"/>',
        f"<description>{e(description)}</description>",
        "<language>de</language>",
        f"<lastBuildDate>{_rfc822(None)}</lastBuildDate>",
        "<generator>MyYTLoader</generator>",
        f"<itunes:author>{e(title)}</itunes:author>",
        f"<itunes:summary>{e(description)}</itunes:summary>",
        f'<itunes:image href="{e(image_url)}"/>',
        f"<image><url>{e(image_url)}</url><title>{e(title)}</title><link>{e(base)}/</link></image>",
        "<itunes:explicit>false</itunes:explicit>",
        "<itunes:type>episodic</itunes:type>",
        "<itunes:block>Yes</itunes:block>",
        '<itunes:category text="Personal Journals"/>',
    ]
    for m in items:
        filename = quote(m["filename"])
        enclosure = f"{base}/feed/{token}/media/{m['id']}/{filename}"
        desc = m.get("description") or ""
        out += [
            "<item>",
            f"<title>{e(m['title'])}</title>",
            f'<guid isPermaLink="false">ytloader-{m["id"]}</guid>',
            f"<pubDate>{_rfc822(m.get('created_at'))}</pubDate>",
            f'<enclosure url="{e(enclosure)}" length="{int(m.get("size") or 0)}" '
            f'type="{MIME_TYPES.get(m["format"], "application/octet-stream")}"/>',
            f"<itunes:duration>{_duration(m.get('duration'))}</itunes:duration>",
            f"<itunes:author>{e(m.get('artist') or '')}</itunes:author>",
            f"<description>{e(desc[:4000])}</description>",
            f"<itunes:summary>{e(desc[:4000])}</itunes:summary>",
            "<itunes:explicit>false</itunes:explicit>",
        ]
        if m.get("source_url"):
            out.append(f"<link>{e(m['source_url'])}</link>")
        if m.get("has_cover"):
            out.append(f'<itunes:image href="{e(base)}/feed/{token}/cover/{m["id"]}.jpg"/>')
        out.append("</item>")
    out += ["</channel>", "</rss>"]
    return "\n".join(out)
