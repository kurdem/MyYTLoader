"""Eigene Feeds: frei zusammengestellte Episodenlisten mit Titel, Cover und Reihenfolge (Episoden oder Serie)."""
from __future__ import annotations

import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Settings
from .db import Database, utcnow
from .library import Library
from .media import FFmpeg
from .security import UserError, safe_join

TYPES = ("episodic", "serial")
SORTS = ("track", "title", "created", "published")
MAX_TITLE = 120
MAX_DESCRIPTION = 4000
MAX_ITEMS = 5000
COVER_SIZE = 1400


def _clean(text: str | None, limit: int, required: bool = False) -> str | None:
    text = unicodedata.normalize("NFC", text or "")
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C").strip()
    if required and not text:
        raise UserError("Bitte einen Titel eingeben.")
    if len(text) > limit:
        raise UserError(f"Höchstens {limit} Zeichen erlaubt.")
    return text or None


class CustomFeeds:
    def __init__(self, settings: Settings, db: Database, library: Library, ffmpeg: FFmpeg):
        self.s = settings
        self.db = db
        self.library = library
        self.ffmpeg = ffmpeg

    # ------------------------------------------------------------ Abfragen
    def _row(self, feed_id: str, user: dict | None) -> dict:
        row = self.db.one("SELECT * FROM feeds WHERE id=?", (feed_id,))
        # Feeds sind persönlich: auch Admins sehen nur ihre eigenen (fremde -> 404)
        if not row or (user is not None and row["user_id"] != user["id"]):
            raise UserError("Feed nicht gefunden.", status_code=404)
        return row

    def _summary(self, row: dict) -> dict:
        stats = self.db.one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(m.duration), 0) AS d "
            "FROM feed_items fi JOIN media m ON m.id = fi.media_id WHERE fi.feed_id=?", (row["id"],)
        ) or {}
        out = {k: v for k, v in row.items() if k != "cover_path"}
        out["has_cover"] = bool(row.get("cover_path"))
        out["item_count"] = stats.get("n", 0)
        out["total_duration"] = stats.get("d", 0)
        return out

    def list(self, user_id: str) -> list[dict]:
        rows = self.db.all("SELECT * FROM feeds WHERE user_id=? ORDER BY title COLLATE NOCASE", (user_id,))
        return [self._summary(r) for r in rows]

    def get(self, feed_id: str, user: dict | None) -> dict:
        return self._summary(self._row(feed_id, user))

    def items(self, feed_id: str) -> list[dict]:
        """Episoden in Feed-Reihenfolge (position aufsteigend)."""
        rows = self.db.all(
            "SELECT m.*, u.username AS owner, fi.position, fi.added_at FROM feed_items fi "
            "JOIN media m ON m.id = fi.media_id LEFT JOIN users u ON u.id = m.user_id "
            "WHERE fi.feed_id=? ORDER BY fi.position, fi.added_at", (feed_id,)
        )
        return [self.library._to_api(r) | {"position": r["position"], "added_at": r["added_at"]} for r in rows]

    def detail(self, feed_id: str, user: dict) -> dict:
        return {**self.get(feed_id, user), "items": self.items(feed_id)}

    def feeds_of_media(self, media_id: str, user_id: str) -> list[str]:
        return [r["feed_id"] for r in self.db.all(
            "SELECT fi.feed_id FROM feed_items fi JOIN feeds f ON f.id = fi.feed_id "
            "WHERE fi.media_id=? AND f.user_id=?", (media_id, user_id))]

    # ------------------------------------------------------------ Ändern
    def create(self, user: dict, title: str, description: str | None = None, type_: str = "episodic",
               collection: str | None = None, media_ids: list[str] | None = None) -> dict:
        if type_ not in TYPES:
            raise UserError("Ungültiger Feed-Typ.")
        feed = {
            "id": uuid.uuid4().hex,
            "user_id": user["id"],
            "title": _clean(title, MAX_TITLE, required=True),
            "description": _clean(description, MAX_DESCRIPTION),
            "type": type_,
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        self.db.insert("feeds", feed)
        ids = list(media_ids or [])
        if collection:
            ids += [m["id"] for m in self._sorted(
                self.library.list(user["id"], collection=collection, limit=MAX_ITEMS), "track")]
        if ids:
            self.add(feed["id"], user, ids)
        return self.detail(feed["id"], user)

    def update(self, feed_id: str, user: dict, changes: dict) -> dict:
        self._row(feed_id, user)
        data: dict = {}
        if "title" in changes and changes["title"] is not None:
            data["title"] = _clean(changes["title"], MAX_TITLE, required=True)
        if "description" in changes:
            data["description"] = _clean(changes["description"], MAX_DESCRIPTION)
        if changes.get("type") is not None:
            if changes["type"] not in TYPES:
                raise UserError("Ungültiger Feed-Typ.")
            data["type"] = changes["type"]
        if data:
            data["updated_at"] = utcnow()
            self.db.update("feeds", feed_id, data)
        return self.detail(feed_id, user)

    def delete(self, feed_id: str, user: dict | None) -> None:
        row = self._row(feed_id, user)
        self._remove_cover(row)
        self.db.execute("DELETE FROM feeds WHERE id=?", (feed_id,))

    def delete_all_of(self, user_id: str) -> None:
        for row in self.db.all("SELECT id FROM feeds WHERE user_id=?", (user_id,)):
            self.delete(row["id"], None)

    def _owned_media(self, user: dict, media_ids: list[str]) -> list[str]:
        ids = list(dict.fromkeys(media_ids))  # Duplikate entfernen, Reihenfolge behalten
        if len(ids) > MAX_ITEMS:
            raise UserError(f"Höchstens {MAX_ITEMS} Episoden pro Feed.")
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        found = {r["id"] for r in self.db.all(
            f"SELECT id FROM media WHERE id IN ({marks}) AND user_id=?", (*ids, user["id"]))}
        if len(found) != len(ids):
            raise UserError("Mindestens eine Datei wurde nicht gefunden.", status_code=404)
        return ids

    def add(self, feed_id: str, user: dict, media_ids: list[str]) -> dict:
        """Hängt Dateien ans Ende an (bereits enthaltene bleiben an ihrer Position)."""
        self._row(feed_id, user)
        ids = self._owned_media(user, media_ids)
        row = self.db.one("SELECT COALESCE(MAX(position), 0) AS p FROM feed_items WHERE feed_id=?", (feed_id,))
        pos = row["p"] if row else 0
        now = utcnow()
        for mid in ids:
            if self.db.one("SELECT 1 FROM feed_items WHERE feed_id=? AND media_id=?", (feed_id, mid)):
                continue
            pos += 1
            self.db.insert("feed_items", {"feed_id": feed_id, "media_id": mid, "position": pos, "added_at": now})
        self._touch(feed_id)
        return self.detail(feed_id, user)

    def insert(self, feed_id: str, user: dict, media: dict) -> dict:
        """Für Abos: in Serien nach Teil-Nr. einsortieren, sonst ans Ende hängen."""
        feed = self._row(feed_id, user)
        if feed["type"] != "serial" or not media.get("track"):
            return self.add(feed_id, user, [media["id"]])
        ids = [m["id"] for m in self.items(feed_id) if m["id"] != media["id"]]
        tracks = {m["id"]: m.get("track") for m in self.items(feed_id)}
        pos = next((i for i, mid in enumerate(ids) if tracks.get(mid) and tracks[mid] > media["track"]), len(ids))
        return self.set_items(feed_id, user, ids[:pos] + [media["id"]] + ids[pos:])

    def set_items(self, feed_id: str, user: dict, media_ids: list[str]) -> dict:
        """Ersetzt Inhalt und Reihenfolge vollständig (für Umsortieren und Entfernen)."""
        self._row(feed_id, user)
        ids = self._owned_media(user, media_ids)
        existing = {r["media_id"]: r["added_at"] for r in
                    self.db.all("SELECT media_id, added_at FROM feed_items WHERE feed_id=?", (feed_id,))}
        now = utcnow()
        self.db.execute("DELETE FROM feed_items WHERE feed_id=?", (feed_id,))
        for pos, mid in enumerate(ids, start=1):
            self.db.insert("feed_items", {"feed_id": feed_id, "media_id": mid, "position": pos,
                                          "added_at": existing.get(mid, now)})
        self._touch(feed_id)
        return self.detail(feed_id, user)

    def remove(self, feed_id: str, user: dict, media_id: str) -> dict:
        self._row(feed_id, user)
        self.db.execute("DELETE FROM feed_items WHERE feed_id=? AND media_id=?", (feed_id, media_id))
        self._touch(feed_id)
        return self.detail(feed_id, user)

    @staticmethod
    def _sorted(items: list[dict], by: str) -> list[dict]:
        if by == "title":
            return sorted(items, key=lambda m: (m["title"] or "").casefold())
        if by == "published":
            return sorted(items, key=lambda m: (m.get("published_at") or "9999", m["created_at"]))
        if by == "created":
            return sorted(items, key=lambda m: m["created_at"])
        # track: Teil-Nr. zuerst, ohne Nummer danach nach Titel
        return sorted(items, key=lambda m: (m.get("track") is None, m.get("track") or 0,
                                            (m["title"] or "").casefold()))

    def sort(self, feed_id: str, user: dict, by: str) -> dict:
        if by not in SORTS:
            raise UserError("Ungültige Sortierung.")
        self._row(feed_id, user)
        ordered = self._sorted(self.items(feed_id), by)
        return self.set_items(feed_id, user, [m["id"] for m in ordered])

    def _touch(self, feed_id: str) -> None:
        self.db.update("feeds", feed_id, {"updated_at": utcnow()})

    # ------------------------------------------------------------ Cover
    def cover_file(self, feed_id: str, user: dict | None) -> Path:
        row = self._row(feed_id, user)
        if row.get("cover_path"):
            path = safe_join(self.s.download_path, row["cover_path"])
            if path.is_file():
                return path
        # Fallback: Cover der ersten Episode
        first = self.db.one(
            "SELECT m.cover_path FROM feed_items fi JOIN media m ON m.id = fi.media_id "
            "WHERE fi.feed_id=? AND m.cover_path IS NOT NULL ORDER BY fi.position LIMIT 1", (feed_id,))
        if first:
            path = safe_join(self.s.download_path, first["cover_path"])
            if path.is_file():
                return path
        raise UserError("Kein Cover vorhanden.", status_code=404)

    def set_cover(self, feed_id: str, user: dict, src: Path) -> dict:
        row = self._row(feed_id, user)
        probe = self.ffmpeg.probe(src)
        video = self.ffmpeg.stream(probe, "video")
        if not video or video.get("codec_name") not in ("png", "mjpeg", "webp"):
            raise UserError("Bitte ein Bild im Format PNG, JPG oder WebP hochladen.")
        if min(int(video.get("width") or 0), int(video.get("height") or 0)) < 300:
            raise UserError("Das Bild ist zu klein (mindestens 300×300 px, ideal 1400×1400 px).")
        folder = self.s.covers_path / "feeds"
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{feed_id}-{uuid.uuid4().hex[:8]}.jpg"
        vf = f"crop='min(iw,ih)':'min(iw,ih)',scale={COVER_SIZE}:{COVER_SIZE}:flags=lanczos,format=yuvj444p"
        self.ffmpeg._run(["-i", str(src), "-frames:v", "1", "-vf", vf, "-q:v", "2", "-f", "image2", str(dest)])
        self._remove_cover(row)
        self.db.update("feeds", feed_id, {"cover_path": str(dest.relative_to(self.s.download_path)),
                                          "updated_at": utcnow()})
        return self.detail(feed_id, user)

    def reset_cover(self, feed_id: str, user: dict) -> dict:
        row = self._row(feed_id, user)
        self._remove_cover(row)
        self.db.update("feeds", feed_id, {"cover_path": None, "updated_at": utcnow()})
        return self.detail(feed_id, user)

    def _remove_cover(self, row: dict) -> None:
        if row.get("cover_path"):
            try:
                safe_join(self.s.download_path, row["cover_path"]).unlink(missing_ok=True)
            except (OSError, UserError):
                pass

    # ------------------------------------------------------------ RSS
    def rss_items(self, feed: dict) -> list[dict]:
        """Episoden für den RSS-Feed. Serie: aufsteigend mit künstlich aufsteigendem Datum,
        damit auch Apps, die nur nach Datum sortieren, Teil 1 vor Teil 2 einordnen."""
        items = self.items(feed["id"])
        if feed["type"] == "serial":
            try:
                base = datetime.fromisoformat(feed["created_at"])
            except ValueError:
                base = datetime.now(timezone.utc)
            base = base - timedelta(days=1)
            for i, m in enumerate(items):
                m["pub_date"] = (base + timedelta(minutes=i)).isoformat()
            return items
        for m in items:
            m["pub_date"] = m["added_at"]
        return sorted(items, key=lambda m: (m["pub_date"], m["position"]), reverse=True)
