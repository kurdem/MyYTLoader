"""Medienbibliothek: Abfragen, Metadaten bearbeiten, Löschen – jeweils pro Benutzer."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .auth import is_admin
from .config import Settings
from .db import Database
from .media import Metadata, write_tags
from .security import UserError, media_basename, safe_join, sanitize_filename, unique_path
from .users import Users

log = logging.getLogger("ytloader.library")

SORTS = {
    "created": "m.created_at",
    "title": "m.title COLLATE NOCASE",
    "artist": "m.artist COLLATE NOCASE",
    "size": "m.size",
    "duration": "m.duration",
}
SELECT = "SELECT m.*, u.username AS owner FROM media m LEFT JOIN users u ON u.id = m.user_id"


class Library:
    def __init__(self, settings: Settings, db: Database, users: Users, bus=None):
        self.s = settings
        self.db = db
        self.users = users
        self.bus = bus

    def _to_api(self, row: dict) -> dict:
        out = dict(row)
        out["filename"] = Path(row["path"]).name
        out["has_cover"] = bool(row.get("cover_path"))
        out["has_original"] = bool(row.get("original_path"))
        out.pop("original_path", None)
        return out

    def _publish(self, event: dict, user_id: str | None) -> None:
        if self.bus:
            self.bus.publish({**event, "user_id": user_id})

    @staticmethod
    def _check(row: dict | None, user: dict | None) -> dict:
        """Nicht vorhanden und fremd sehen gleich aus (404) – keine Informationen preisgeben."""
        if not row or (user is not None and not is_admin(user) and row.get("user_id") != user["id"]):
            raise UserError("Medium nicht gefunden.", status_code=404)
        return row

    def raw(self, media_id: str, user: dict | None = None) -> dict:
        return self._check(self.db.one(f"{SELECT} WHERE m.id=?", (media_id,)), user)

    def get(self, media_id: str, user: dict | None = None) -> dict:
        return self._to_api(self.raw(media_id, user))

    def file_path(self, media_id: str, user: dict | None = None) -> Path:
        row = self.raw(media_id, user)
        path = safe_join(self.s.download_path, row["path"])
        if not path.is_file():
            raise UserError("Die Datei fehlt auf dem Datenträger.", status_code=404)
        return path

    def cover_file(self, media_id: str, user: dict | None = None) -> Path:
        row = self.raw(media_id, user)
        if not row.get("cover_path"):
            raise UserError("Kein Cover vorhanden.", status_code=404)
        path = safe_join(self.s.download_path, row["cover_path"])
        if not path.is_file():
            raise UserError("Kein Cover vorhanden.", status_code=404)
        return path

    def list(self, user_id: str | None, q: str | None = None, kind: str | None = None,
             collection: str | None = None, sort: str = "created", order: str = "desc",
             limit: int = 1000, offset: int = 0) -> list[dict]:
        """user_id=None listet alle Benutzer (nur für Admins verwenden)."""
        where, params = [], []
        if user_id:
            where.append("m.user_id=?")
            params.append(user_id)
        if q:
            like = f"%{q.strip()}%"
            where.append("(m.title LIKE ? OR m.artist LIKE ? OR m.collection LIKE ? OR m.description LIKE ?)")
            params += [like, like, like, like]
        if kind in ("audio", "video"):
            where.append("m.kind=?")
            params.append(kind)
        if collection:
            where.append("m.collection=?")
            params.append(collection)
        sql = SELECT
        if where:
            sql += " WHERE " + " AND ".join(where)
        direction = "ASC" if order == "asc" else "DESC"
        sql += f" ORDER BY {SORTS.get(sort, 'm.created_at')} {direction}, m.rowid {direction} LIMIT ? OFFSET ?"
        params += [max(1, min(limit, 5000)), max(0, offset)]
        return [self._to_api(r) for r in self.db.all(sql, params)]

    def collections(self, user_id: str | None) -> list[dict]:
        sql = ("SELECT collection AS name, COUNT(*) AS count, SUM(size) AS size, MAX(created_at) AS updated_at "
               "FROM media WHERE collection IS NOT NULL AND collection<>''")
        params: tuple = ()
        if user_id:
            sql += " AND user_id=?"
            params = (user_id,)
        return self.db.all(sql + " GROUP BY collection ORDER BY collection", params)

    def stats(self, user_id: str | None = None) -> dict:
        sql = "SELECT COUNT(*) AS count, COALESCE(SUM(size + original_size),0) AS size FROM media"
        row = self.db.one(sql + (" WHERE user_id=?" if user_id else ""), (user_id,) if user_id else ()) or {}
        return {"count": row.get("count", 0), "size": row.get("size", 0)}

    def update(self, media_id: str, changes: dict, user: dict | None = None) -> dict:
        row = self.raw(media_id, user)
        title = (changes.get("title") if changes.get("title") is not None else row["title"]).strip()
        if not title:
            raise UserError("Der Titel darf nicht leer sein.")
        artist = changes.get("artist", row["artist"])
        artist = artist.strip() or None if isinstance(artist, str) else artist
        description = changes.get("description", row["description"])
        collection = changes.get("collection", row["collection"])
        collection = sanitize_filename(collection, fallback="") or None if isinstance(collection, str) else collection

        old_path = safe_join(self.s.download_path, row["path"])
        folder = self.users.folder(self.users.get(row.get("user_id")))
        if collection:
            folder = safe_join(folder, collection)
        folder.mkdir(parents=True, exist_ok=True)
        new_path = folder / f"{media_basename(artist, title)}.{row['format']}"
        if new_path != old_path:
            new_path = unique_path(new_path)
            old_path.rename(new_path)
            self._remove_empty_dir(old_path.parent)

        cover = safe_join(self.s.download_path, row["cover_path"]) if row.get("cover_path") else None
        write_tags(new_path, row["format"], Metadata(
            title=title[:300], artist=artist, album=collection or artist, description=description,
            date=row.get("published_at"), url=row.get("source_url"),
        ), cover)
        self.db.update("media", media_id, {
            "title": title[:300], "artist": artist, "description": description, "collection": collection,
            "path": str(new_path.relative_to(self.s.download_path)), "size": new_path.stat().st_size,
        })
        media = self.get(media_id)
        self._publish({"type": "media", "media": media}, row.get("user_id"))
        return media

    def delete(self, media_id: str, user: dict | None = None) -> None:
        row = self.raw(media_id, user)
        for key in ("path", "cover_path", "original_path"):
            if row.get(key):
                try:
                    p = safe_join(self.s.download_path, row[key])
                    p.unlink(missing_ok=True)
                    if key == "path":
                        self._remove_empty_dir(p.parent)
                except (OSError, UserError) as exc:
                    log.warning("Konnte %s nicht löschen: %s", row[key], exc)
        self.db.execute("DELETE FROM media WHERE id=?", (media_id,))
        self._publish({"type": "media_deleted", "id": media_id}, row.get("user_id"))

    def delete_older_than(self, days: int, user_id: str | None = None) -> int:
        """user_id=None löscht über alle Benutzer (Aufbewahrungsfrist / Admin)."""
        if days < 0:
            raise UserError("Ungültige Anzahl Tage.")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()
        sql = "SELECT id FROM media WHERE created_at < ?"
        params: list = [cutoff]
        if user_id:
            sql += " AND user_id=?"
            params.append(user_id)
        rows = self.db.all(sql, params)
        for r in rows:
            self.delete(r["id"])
        return len(rows)

    def delete_all_of(self, user_id: str) -> int:
        rows = self.db.all("SELECT id FROM media WHERE user_id=?", (user_id,))
        for r in rows:
            self.delete(r["id"])
        return len(rows)

    def _remove_empty_dir(self, folder: Path) -> None:
        try:
            base = self.s.download_path.resolve()
            folder = folder.resolve()
            while folder != base and base in folder.parents and not any(folder.iterdir()):
                folder.rmdir()
                folder = folder.parent
        except OSError:
            pass
