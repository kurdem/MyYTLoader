"""Automatische Abos: YouTube-Kanäle und Playlists regelmäßig prüfen und neue Videos laden.

- Beim Anlegen werden alle vorhandenen Videos als "gesehen" markiert – außer denen, die
  ausdrücklich initial geladen werden sollen (keine, die letzten N oder alle).
- Ein Video gilt erst als gesehen, wenn sein Auftrag angelegt wurde. Scheitert das (z. B. Kontingent
  erschöpft), wird es bei der nächsten Prüfung erneut versucht.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from .auth import is_admin
from .config import ALL_FORMATS, Settings
from .customfeeds import CustomFeeds
from .db import Database, utcnow
from .jobs import JobManager
from .security import UserError, sanitize_filename, validate_url, youtube_video_url
from .users import Users
from .ytdlp import YtDlp

log = logging.getLogger("ytloader.subscriptions")

MIN_INTERVAL_H = 1
MAX_INTERVAL_H = 168
INITIAL_MODES = ("none", "latest", "all")
_CHANNEL = re.compile(r"^/(@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)(/(videos|streams|podcasts|featured))?/?$")
_PLAYLIST_ID = re.compile(r"^[A-Za-z0-9_-]{10,64}$")


def normalize_source(url: str) -> tuple[str, str]:
    """Liefert (kanonische URL, Art). Kanäle zeigen auf ihren Video-Tab, Playlists auf /playlist?list=…"""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    list_id = (parse_qs(parts.query).get("list") or [None])[0]
    if list_id and _PLAYLIST_ID.match(list_id) and (parts.path in ("/playlist", "/watch") or host == "youtu.be"):
        return f"https://www.youtube.com/playlist?list={list_id}", "playlist"
    m = _CHANNEL.match(parts.path)
    if m:
        tab = m.group(3) or "videos"
        if tab == "featured":
            tab = "videos"
        return f"https://www.youtube.com/{m.group(1)}/{tab}", "channel"
    raise UserError("Bitte einen Kanal-Link (z. B. youtube.com/@name) oder einen Playlist-Link angeben.")


def _chronological(info: dict, kind: str) -> list[tuple[int, dict]]:
    """(Position in der Quelle, Eintrag) von alt nach neu. Kanäle listen neueste zuerst, Playlists in Listenreihenfolge."""
    indexed = list(enumerate(info["entries"], start=1))
    return list(reversed(indexed)) if kind == "channel" else indexed


def _clean_title(title: str | None) -> str:
    title = (title or "Abo").strip()
    return re.sub(r"\s+-\s+(Videos|Livestreams|Podcasts)$", "", title)[:120] or "Abo"


class Subscriptions:
    def __init__(self, settings: Settings, db: Database, ytdlp: YtDlp, jobs: JobManager, users: Users,
                 feeds: CustomFeeds):
        self.s = settings
        self.db = db
        self.ytdlp = ytdlp
        self.jobs = jobs
        self.users = users
        self.feeds = feeds
        self._lock = threading.Lock()
        jobs.after_done.append(self._on_job_done)

    # ------------------------------------------------------------ Abfragen
    def _to_api(self, row: dict) -> dict:
        out = dict(row)
        out["options"] = json.loads(row.get("options") or "{}")
        out["enabled"] = bool(row["enabled"])
        out["next_check_at"] = self._next(row)
        return out

    @staticmethod
    def _next(row: dict) -> str | None:
        if not row["enabled"]:
            return None
        if not row.get("last_checked_at"):
            return utcnow()
        try:
            last = datetime.fromisoformat(row["last_checked_at"])
        except ValueError:
            return utcnow()
        return (last + timedelta(hours=row["interval_hours"])).replace(microsecond=0).isoformat()

    def _row(self, sub_id: str, user: dict | None) -> dict:
        row = self.db.one("SELECT s.*, u.username AS owner FROM subscriptions s "
                          "LEFT JOIN users u ON u.id = s.user_id WHERE s.id=?", (sub_id,))
        if not row or (user is not None and not is_admin(user) and row["user_id"] != user["id"]):
            raise UserError("Abo nicht gefunden.", status_code=404)
        return row

    def get(self, sub_id: str, user: dict | None) -> dict:
        return self._to_api(self._row(sub_id, user))

    def list(self, user: dict) -> list[dict]:
        sql = "SELECT s.*, u.username AS owner FROM subscriptions s LEFT JOIN users u ON u.id = s.user_id"
        params: tuple = ()
        if not is_admin(user):
            sql += " WHERE s.user_id=?"
            params = (user["id"],)
        return [self._to_api(r) for r in self.db.all(sql + " ORDER BY s.title COLLATE NOCASE", params)]

    def due(self) -> list[str]:
        now = datetime.now(timezone.utc)
        rows = self.db.all("SELECT s.* FROM subscriptions s JOIN users u ON u.id = s.user_id "
                           "WHERE s.enabled=1 AND u.disabled=0")
        result = []
        for r in rows:
            nxt = self._next(r)
            try:
                if nxt and datetime.fromisoformat(nxt) <= now:
                    result.append(r["id"])
            except ValueError:
                result.append(r["id"])
        return result

    # ------------------------------------------------------------ Anlegen/Ändern
    @staticmethod
    def _interval(value) -> int:
        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise UserError("Ungültiges Prüfintervall.") from exc
        if not MIN_INTERVAL_H <= value <= MAX_INTERVAL_H:
            raise UserError(f"Das Prüfintervall muss zwischen {MIN_INTERVAL_H} und {MAX_INTERVAL_H} Stunden liegen.")
        return value

    def create(self, user: dict, *, url: str, fmt: str, options: dict, interval_hours: int = 6,
               initial: str = "none", initial_count: int = 3, collection: str | None = None,
               feed_mode: str = "none", feed_id: str | None = None, feed_type: str | None = None) -> dict:
        if fmt not in ALL_FORMATS:
            raise UserError("Unbekanntes Ausgabeformat.")
        if initial not in INITIAL_MODES:
            raise UserError("Ungültige Auswahl für den ersten Abruf.")
        interval = self._interval(interval_hours)
        source, kind = normalize_source(validate_url(url, self.s.allowed_domains, self.s.check_dns))
        if self.db.one("SELECT 1 FROM subscriptions WHERE user_id=? AND url=?", (user["id"], source)):
            raise UserError("Diesen Kanal bzw. diese Playlist hast du bereits abonniert.", status_code=409)
        info = self.ytdlp.analyze(source, self.s.playlist_max_items)
        if info.get("type") != "playlist":
            raise UserError("Unter diesem Link wurde keine Video-Liste gefunden.")
        title = _clean_title(info.get("title"))
        folder = sanitize_filename(collection or title, fallback="") or "Abo"

        target_feed = None
        if feed_mode == "new":
            target_feed = self.feeds.create(user, title, info.get("description"),
                                            feed_type or ("serial" if kind == "playlist" else "episodic"))["id"]
        elif feed_mode == "existing":
            if not feed_id:
                raise UserError("Bitte einen Feed auswählen.")
            target_feed = self.feeds.get(feed_id, user)["id"]
        elif feed_mode != "none":
            raise UserError("Ungültige Feed-Auswahl.")

        sub = {
            "id": uuid.uuid4().hex,
            "user_id": user["id"],
            "url": source,
            "kind": kind,
            "title": title,
            "format": fmt,
            "options": json.dumps(options),
            "collection": folder,
            "feed_id": target_feed,
            "interval_hours": interval,
            "enabled": 1,
            "last_checked_at": utcnow(),
            "created_at": utcnow(),
        }
        self.db.insert("subscriptions", sub)

        entries = _chronological(info, kind)
        if initial == "all":
            pick = entries
        elif initial == "latest":
            n = max(0, min(int(initial_count or 0), len(entries)))
            pick = entries[len(entries) - n:] if n else []
        else:
            pick = []
        picked_ids = {e["id"] for _, e in pick}
        self._mark_seen(sub["id"], [e["id"] for _, e in entries if e["id"] not in picked_ids])
        created, error = self._enqueue(sub, user, pick)
        self.db.update("subscriptions", sub["id"], {"last_new_count": created, "last_error": error})
        log.info("Abo angelegt: %s (%s), %d initiale Downloads", title, source, created)
        return self.get(sub["id"], user)

    def update(self, sub_id: str, user: dict, changes: dict) -> dict:
        row = self._row(sub_id, user)
        owner = self.users.require(row["user_id"])
        data: dict = {}
        if changes.get("enabled") is not None:
            data["enabled"] = 1 if changes["enabled"] else 0
        if changes.get("interval_hours") is not None:
            data["interval_hours"] = self._interval(changes["interval_hours"])
        if changes.get("format") is not None:
            if changes["format"] not in ALL_FORMATS:
                raise UserError("Unbekanntes Ausgabeformat.")
            data["format"] = changes["format"]
        if changes.get("options") is not None:
            data["options"] = json.dumps(changes["options"])
        if changes.get("collection") is not None:
            data["collection"] = sanitize_filename(changes["collection"], fallback="") or row["collection"]
        if "feed_id" in changes:
            data["feed_id"] = self.feeds.get(changes["feed_id"], owner)["id"] if changes["feed_id"] else None
        if data:
            self.db.update("subscriptions", sub_id, data)
        return self.get(sub_id, user)

    def delete(self, sub_id: str, user: dict) -> None:
        self._row(sub_id, user)
        self.db.execute("DELETE FROM subscriptions WHERE id=?", (sub_id,))

    def delete_all_of(self, user_id: str) -> None:
        self.db.execute("DELETE FROM subscriptions WHERE user_id=?", (user_id,))

    # ------------------------------------------------------------ Prüfen
    def check(self, sub_id: str, user: dict | None = None) -> dict:
        """Prüft auf neue Videos und legt Aufträge an. Läuft nie parallel zu einer anderen Prüfung."""
        if not self._lock.acquire(blocking=False):
            raise UserError("Es läuft gerade eine Abo-Prüfung. Bitte gleich erneut versuchen.", status_code=409)
        try:
            row = self._row(sub_id, user)
            owner = self.users.get(row["user_id"])
            if not owner or owner["disabled"]:
                raise UserError("Der Besitzer dieses Abos ist gesperrt.", status_code=409)
            try:
                info = self.ytdlp.analyze(row["url"], self.s.playlist_max_items)
                if info.get("type") != "playlist":
                    raise UserError("Unter diesem Link wurde keine Video-Liste gefunden.")
            except UserError as exc:
                self.db.update("subscriptions", sub_id, {"last_checked_at": utcnow(), "last_error": exc.message,
                                                         "last_new_count": 0})
                log.warning("Abo %s: %s", row["title"], exc.message)
                return self.get(sub_id, user)
            seen = {r["video_id"] for r in self.db.all(
                "SELECT video_id FROM subscription_seen WHERE subscription_id=?", (sub_id,))}
            new = [(pos, e) for pos, e in _chronological(info, row["kind"]) if e["id"] not in seen]
            created, error = self._enqueue(row, owner, new)
            self.db.update("subscriptions", sub_id, {
                "last_checked_at": utcnow(), "last_error": error, "last_new_count": created,
            })
            if created:
                log.info("Abo %s: %d neue Videos", row["title"], created)
            return self.get(sub_id, user)
        finally:
            self._lock.release()

    def _mark_seen(self, sub_id: str, video_ids: list[str]) -> None:
        now = utcnow()
        for vid in video_ids:
            self.db.execute("INSERT OR IGNORE INTO subscription_seen(subscription_id, video_id, seen_at) "
                            "VALUES(?,?,?)", (sub_id, vid, now))

    def _enqueue(self, sub: dict, owner: dict, entries: list[tuple[int, dict]]) -> tuple[int, str | None]:
        options = json.loads(sub["options"]) if isinstance(sub["options"], str) else dict(sub["options"])
        created = 0
        for pos, e in entries:
            opts = dict(options)
            if sub["kind"] == "playlist":
                opts["track"] = pos
            try:
                self.jobs.create(
                    user=owner, url=youtube_video_url(e["id"]), fmt=sub["format"], options=opts,
                    title=e.get("title"), channel=e.get("channel"), thumbnail=e.get("thumbnail"),
                    duration=e.get("duration"), collection=sub["collection"], subscription_id=sub["id"],
                )
            except UserError as exc:
                return created, exc.message  # z. B. Kontingent – Rest beim nächsten Mal
            self._mark_seen(sub["id"], [e["id"]])
            created += 1
        return created, None

    def _on_job_done(self, job: dict, media: dict) -> None:
        sub_id = job.get("subscription_id")
        if not sub_id:
            return
        row = self.db.one("SELECT * FROM subscriptions WHERE id=?", (sub_id,))
        if not row or not row.get("feed_id"):
            return
        owner = self.users.get(row["user_id"])
        if owner:
            try:
                self.feeds.insert(row["feed_id"], owner, media)
            except UserError as exc:
                log.warning("Abo %s: Feed nicht ergänzt: %s", row["title"], exc.message)
