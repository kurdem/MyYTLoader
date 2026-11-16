"""Medienbibliothek: Abfragen, Metadaten bearbeiten, Löschen."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Settings
from .db import Database
from .media import Metadata, write_tags
from .security import UserError, media_basename, safe_join, sanitize_filename, unique_path

log = logging.getLogger("ytloader.library")

SORTS = {
    "created": "created_at",
    "title": "title COLLATE NOCASE",
    "artist": "artist COLLATE NOCASE",
    "size": "size",
    "duration": "duration",
}


class Library:
    def __init__(self, settings: Settings, db: Database, bus=None):
        self.s = settings
        self.db = db
        self.bus = bus

    def _to_api(self, row: dict) -> dict:
        out = dict(row)
        out["filename"] = Path(row["path"]).name
        out["has_cover"] = bool(row.get("cover_path"))
        out.pop("original_path", None)
        out["has_original"] = bool(row.get("original_path"))
        return out

    def get(self, media_id: str) -> dict:
        row = self.db.one("SELECT * FROM media WHERE id=?", (media_id,))
        if not row:
            raise UserError("Medium nicht gefunden.", status_code=404)
        return self._to_api(row)

    def raw(self, media_id: str) -> dict:
        row = self.db.one("SELECT * FROM media WHERE id=?", (media_id,))
        if not row:
            raise UserError("Medium nicht gefunden.", status_code=404)
        return row

    def file_path(self, media_id: str) -> Path:
        row = self.raw(media_id)
        path = safe_join(self.s.download_path, row["path"])
        if not path.is_file():
            raise UserError("Die Datei fehlt auf dem Datenträger.", status_code=404)
        return path

    def cover_file(self, media_id: str) -> Path:
        row = self.raw(media_id)
        if not row.get("cover_path"):
            raise UserError("Kein Cover vorhanden.", status_code=404)
        path = safe_join(self.s.download_path, row["cover_path"])
        if not path.is_file():
            raise UserError("Kein Cover vorhanden.", status_code=404)
        return path

    def list(self, q: str | None = None, kind: str | None = None, collection: str | None = None,
             sort: str = "created", order: str = "desc", limit: int = 1000, offset: int = 0) -> list[dict]:
        where, params = [], []
        if q:
            like = f"%{q.strip()}%"
            where.append("(title LIKE ? OR artist LIKE ? OR collection LIKE ? OR description LIKE ?)")
            params += [like, like, like, like]
        if kind in ("audio", "video"):
            where.append("kind=?")
            params.append(kind)
        if collection:
            where.append("collection=?")
            params.append(collection)
        sql = "SELECT * FROM media"
        if where:
            sql += " WHERE " + " AND ".join(where)
        direction = "ASC" if order == "asc" else "DESC"
        sql += f" ORDER BY {SORTS.get(sort, 'created_at')} {direction}, rowid {direction} LIMIT ? OFFSET ?"
        params += [max(1, min(limit, 5000)), max(0, offset)]
        return [self._to_api(r) for r in self.db.all(sql, params)]

    def collections(self) -> list[dict]:
        return self.db.all(
            "SELECT collection AS name, COUNT(*) AS count, SUM(size) AS size, MAX(created_at) AS updated_at "
            "FROM media WHERE collection IS NOT NULL AND collection<>'' GROUP BY collection ORDER BY collection"
        )

    def stats(self) -> dict:
        row = self.db.one("SELECT COUNT(*) AS count, COALESCE(SUM(size),0) AS size FROM media") or {}
        return {"count": row.get("count", 0), "size": row.get("size", 0)}

    def update(self, media_id: str, changes: dict) -> dict:
        row = self.raw(media_id)
        title = (changes.get("title") if changes.get("title") is not None else row["title"]).strip()
        if not title:
            raise UserError("Der Titel darf nicht leer sein.")
        artist = changes.get("artist", row["artist"])
        artist = artist.strip() or None if isinstance(artist, str) else artist
        description = changes.get("description", row["description"])
        collection = changes.get("collection", row["collection"])
        collection = sanitize_filename(collection, fallback="") or None if isinstance(collection, str) else collection

        old_path = safe_join(self.s.download_path, row["path"])
        folder = self.s.download_path
        if collection:
            folder = safe_join(self.s.download_path, collection)
        folder.mkdir(parents=True, exist_ok=True)
        new_path = folder / f"{media_basename(artist, title)}.{row['format']}"
        if new_path != old_path:
            new_path = unique_path(new_path)
            old_path.rename(new_path)
            self._remove_empty_dir(old_path.parent)

        cover = safe_join(self.s.download_path, row["cover_path"]) if row.get("cover_path") else None
        date = row.get("published_at")
        write_tags(new_path, row["format"], Metadata(
            title=title[:300], artist=artist, album=collection or artist, description=description,
            date=date, url=row.get("source_url"),
        ), cover)
        self.db.update("media", media_id, {
            "title": title[:300], "artist": artist, "description": description, "collection": collection,
            "path": str(new_path.relative_to(self.s.download_path)), "size": new_path.stat().st_size,
        })
        media = self.get(media_id)
        if self.bus:
            self.bus.publish({"type": "media", "media": media})
        return media

    def delete(self, media_id: str) -> None:
        row = self.raw(media_id)
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
        if self.bus:
            self.bus.publish({"type": "media_deleted", "id": media_id})

    def delete_older_than(self, days: int) -> int:
        if days < 0:
            raise UserError("Ungültige Anzahl Tage.")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()
        rows = self.db.all("SELECT id FROM media WHERE created_at < ?", (cutoff,))
        for r in rows:
            self.delete(r["id"])
        return len(rows)

    def _remove_empty_dir(self, folder: Path) -> None:
        try:
            base = self.s.download_path.resolve()
            folder = folder.resolve()
            if folder != base and base in folder.parents and not any(folder.iterdir()):
                folder.rmdir()
        except OSError:
            pass
