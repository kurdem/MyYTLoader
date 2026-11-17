"""Hintergrund-Warteschlange: Download -> Verarbeitung -> Bibliothek.

Jobs liegen in SQLite und laufen serverseitig weiter, auch wenn der Browser geschlossen wird.
Nach einem Neustart werden unterbrochene Jobs wieder eingereiht.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .auth import is_admin
from .config import AUDIO_FORMATS, Settings
from .db import Database, job_to_api, utcnow
from .library import Library
from .media import FFmpeg, Metadata, write_tags
from .security import UserError, media_basename, safe_join, sanitize_filename, unique_path
from .users import Users
from .ytdlp import Progress, YtDlp, format_selector

log = logging.getLogger("ytloader.jobs")

ACTIVE = ("queued", "running", "processing")
SELECT = "SELECT j.*, u.username AS owner FROM jobs j LEFT JOIN users u ON u.id = j.user_id"
QUOTA_EXCEEDED = "Dein Speicherkontingent ist erschöpft. Bitte alte Dateien löschen oder den Admin fragen."
DOWNLOAD_SHARE = 0.85  # Anteil des Downloads am Gesamtfortschritt


def _track(value) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if 0 < n < 100000 else None


class EventBus:
    """Verteilt Ereignisse an alle SSE-Clients (thread-sicher)."""

    def __init__(self) -> None:
        # Queue -> (user_id, ist_admin): Ereignisse gehen nur an Besitzer und Admins
        self._subs: dict[asyncio.Queue, tuple[str, bool]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, user: dict) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs[q] = (user["id"], is_admin(user))
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.pop(q, None)

    def publish(self, event: dict) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        try:
            self._loop.call_soon_threadsafe(self._fanout, event)
        except RuntimeError:
            pass

    def _fanout(self, event: dict) -> None:
        owner = event.get("user_id")
        for q, (user_id, admin) in list(self._subs.items()):
            if not admin and (owner is None or owner != user_id):
                continue
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # Client synchronisiert sich beim nächsten Reconnect neu


class JobManager:
    def __init__(self, settings: Settings, db: Database, ytdlp: YtDlp, ffmpeg: FFmpeg,
                 library: Library, users: Users, bus: EventBus):
        self.s = settings
        self.db = db
        self.users = users
        self.ytdlp = ytdlp
        self.ffmpeg = ffmpeg
        self.library = library
        self.bus = bus
        self._lock = threading.Lock()
        self._procs: dict[str, subprocess.Popen] = {}
        self._cancelled: set[str] = set()
        self._wakeup: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: list[asyncio.Task] = []
        self._last_publish: dict[str, float] = {}
        # Hooks nach erfolgreichem Auftrag: fn(job, media) – z. B. Abo -> eigenen Feed ergänzen
        self.after_done: list = []

    # ------------------------------------------------------------ Lebenszyklus
    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wakeup = asyncio.Event()
        n = self.db.execute(
            "UPDATE jobs SET status='queued', stage=NULL, progress=0, speed=NULL, eta=NULL "
            "WHERE status IN ('running','processing')"
        )
        if n:
            log.info("%d unterbrochene Jobs wieder eingereiht", n)
        for i in range(self.s.max_concurrent_downloads):
            self._tasks.append(asyncio.create_task(self._worker(i), name=f"job-worker-{i}"))

    async def stop(self) -> None:
        with self._lock:
            for proc in self._procs.values():
                if proc.poll() is None:
                    proc.terminate()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def wake(self) -> None:
        if self._loop and self._wakeup:
            self._loop.call_soon_threadsafe(self._wakeup.set)

    async def _worker(self, idx: int) -> None:
        assert self._wakeup is not None
        while True:
            job = self._claim()
            if job is None:
                try:
                    await asyncio.wait_for(self._wakeup.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
                self._wakeup.clear()
                continue
            try:
                await asyncio.to_thread(self._run, job)
            except Exception:  # pragma: no cover - Sicherheitsnetz
                log.exception("Worker %d: unerwarteter Fehler", idx)

    def _claim(self) -> dict | None:
        with self._lock:
            # ältester wartender Auftrag, dessen Besitzer sein Limit paralleler Aufträge nicht erreicht hat
            row = self.db.one(
                "SELECT j.* FROM jobs j LEFT JOIN users u ON u.id = j.user_id "
                "WHERE j.status='queued' AND (u.max_jobs IS NULL OR u.max_jobs <= 0 OR "
                "  (SELECT COUNT(*) FROM jobs r WHERE r.user_id = j.user_id "
                "   AND r.status IN ('running','processing')) < u.max_jobs) "
                "ORDER BY j.created_at, j.rowid LIMIT 1"
            )
            if not row:
                return None
            self.db.update("jobs", row["id"], {
                "status": "running", "stage": "Startet", "started_at": utcnow(), "progress": 0, "error": None,
            })
            row["status"] = "running"
            return row

    # ---------------------------------------------------------------- API
    def create(self, *, user: dict, url: str, fmt: str, options: dict, kind: str = "url", title: str | None = None,
               channel: str | None = None, thumbnail: str | None = None, duration: float | None = None,
               collection: str | None = None, subscription_id: str | None = None) -> dict:
        if self.users.remaining_bytes(user) == 0:
            raise UserError(QUOTA_EXCEEDED, status_code=413)
        job = {
            "id": uuid.uuid4().hex,
            "user_id": user["id"],
            "kind": kind,
            "url": url,
            "title": title,
            "channel": channel,
            "thumbnail": thumbnail,
            "duration": duration,
            "format": fmt,
            "options": json.dumps(options),
            "collection": collection,
            "subscription_id": subscription_id,
            "status": "queued",
            "stage": "Wartet",
            "progress": 0,
            "created_at": utcnow(),
        }
        self.db.insert("jobs", job)
        self._publish(job["id"], force=True)
        self.wake()
        return self.get(job["id"])

    def get(self, job_id: str, user: dict | None = None) -> dict:
        row = self.db.one(f"{SELECT} WHERE j.id=?", (job_id,))
        if not row or (user is not None and not is_admin(user) and row["user_id"] != user["id"]):
            raise UserError("Auftrag nicht gefunden.", status_code=404)
        return job_to_api(row)

    def list(self, user: dict, status: str | None = None, limit: int = 500) -> list[dict]:
        """Admins sehen die gesamte Warteschlange, Benutzer nur ihre eigenen Aufträge."""
        groups = {
            "active": ACTIVE,
            "done": ("done",),
            "failed": ("failed", "cancelled"),
        }
        where, params = [], []
        if not is_admin(user):
            where.append("j.user_id=?")
            params.append(user["id"])
        if status in groups:
            where.append(f"j.status IN ({','.join('?' for _ in groups[status])})")
            params += groups[status]
        sql = SELECT + (" WHERE " + " AND ".join(where) if where else "")
        rows = self.db.all(sql + " ORDER BY j.created_at DESC, j.rowid DESC LIMIT ?", (*params, limit))
        return [job_to_api(r) for r in rows]

    def cancel(self, job_id: str, user: dict | None = None) -> dict:
        job = self.get(job_id, user)
        if job["status"] not in ACTIVE:
            raise UserError("Nur laufende oder wartende Aufträge können abgebrochen werden.", 409)
        with self._lock:
            row = self.db.one("SELECT status FROM jobs WHERE id=?", (job_id,))
            if row and row["status"] == "queued":
                self.db.update("jobs", job_id, {"status": "cancelled", "stage": None, "finished_at": utcnow(),
                                                "error": "Abgebrochen."})
                self._cleanup_upload(job)
            else:
                self._cancelled.add(job_id)
                proc = self._procs.get(job_id)
                if proc and proc.poll() is None:
                    proc.terminate()
        self._publish(job_id, force=True)
        return self.get(job_id)

    def retry(self, job_id: str, user: dict | None = None) -> dict:
        job = self.get(job_id, user)
        if job["status"] not in ("failed", "cancelled", "done"):
            raise UserError("Dieser Auftrag läuft noch.", 409)
        if job["kind"] != "url":
            raise UserError("Uploads können nicht wiederholt werden. Bitte Datei erneut hochladen.", 409)
        owner = self.users.get(job["user_id"])
        if owner and self.users.remaining_bytes(owner) == 0:
            raise UserError(QUOTA_EXCEEDED, status_code=413)
        self.db.update("jobs", job_id, {
            "status": "queued", "stage": "Wartet", "progress": 0, "speed": None, "eta": None,
            "error": None, "media_id": None, "started_at": None, "finished_at": None, "created_at": utcnow(),
        })
        self._publish(job_id, force=True)
        self.wake()
        return self.get(job_id)

    def delete(self, job_id: str, user: dict | None = None) -> None:
        job = self.get(job_id, user)
        if job["status"] in ACTIVE:
            raise UserError("Laufende Aufträge bitte zuerst abbrechen.", 409)
        self.db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        self.bus.publish({"type": "job_deleted", "id": job_id, "user_id": job["user_id"]})

    def clear_finished(self, user: dict) -> int:
        """Admins leeren den gesamten Verlauf, Benutzer nur ihren eigenen."""
        sql = "DELETE FROM jobs WHERE status IN ('done','failed','cancelled')"
        if is_admin(user):
            n = self.db.execute(sql)
        else:
            n = self.db.execute(sql + " AND user_id=?", (user["id"],))
        self.bus.publish({"type": "jobs_cleared", "user_id": user["id"]})
        return n

    def cancel_all_of(self, user_id: str) -> None:
        for row in self.db.all("SELECT id FROM jobs WHERE user_id=? AND status IN ('queued','running','processing')",
                               (user_id,)):
            try:
                self.cancel(row["id"])
            except UserError:
                pass

    def is_active(self, job_id: str) -> bool:
        row = self.db.one("SELECT status FROM jobs WHERE id=?", (job_id,))
        return bool(row and row["status"] in ACTIVE)

    # ------------------------------------------------------------ Ausführung
    def _register(self, job_id: str):
        def register(proc: subprocess.Popen | None) -> None:
            with self._lock:
                if proc is None:
                    self._procs.pop(job_id, None)
                else:
                    self._procs[job_id] = proc
                    if job_id in self._cancelled and proc.poll() is None:
                        proc.terminate()
        return register

    def _check_cancel(self, job_id: str) -> None:
        if job_id in self._cancelled:
            raise UserError("Abgebrochen.")

    def _set(self, job_id: str, force: bool = False, **fields) -> None:
        self.db.update("jobs", job_id, fields)
        self._publish(job_id, force=force)

    def _publish(self, job_id: str, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_publish.get(job_id, 0) < 0.5:
            return
        self._last_publish[job_id] = now
        row = self.db.one(f"{SELECT} WHERE j.id=?", (job_id,))
        if row:
            self.bus.publish({"type": "job", "job": job_to_api(row), "user_id": row["user_id"]})

    def _run(self, job: dict) -> None:
        job_id = job["id"]
        opts = json.loads(job.get("options") or "{}")
        fmt = job["format"]
        workdir = self.s.temp_path / "jobs" / job_id
        if workdir.exists():
            shutil.rmtree(workdir, ignore_errors=True)
        workdir.mkdir(parents=True, exist_ok=True)
        register = self._register(job_id)
        source: Path | None = None
        owner = self.users.get(job.get("user_id"))
        try:
            remaining = self.users.remaining_bytes(owner) if owner else None
            if remaining == 0:
                raise UserError(QUOTA_EXCEEDED)
            max_bytes = self.s.max_file_size_bytes if remaining is None else min(self.s.max_file_size_bytes, remaining)
            info: dict = {}
            cover_src: Path | None = None
            if job["kind"] == "upload":
                source = Path(opts["upload_path"])
                if not source.exists():
                    raise UserError("Die hochgeladene Datei ist nicht mehr vorhanden.")
                share = 0.0
            else:
                self._set(job_id, force=True, stage="Download", progress=0)
                selector, merge = format_selector(fmt, opts.get("max_height"))

                def on_progress(p: Progress) -> None:
                    frac = (p.downloaded / p.total) if p.total else 0
                    stage = "Download" if p.stream <= 1 else f"Download (Spur {p.stream})"
                    self._set(job_id, stage=stage, progress=round(min(frac, 1) * DOWNLOAD_SHARE, 4),
                              speed=p.speed, eta=p.eta)

                result = self.ytdlp.download(
                    job["url"], selector, workdir / "dl", merge, max_bytes,
                    self.s.ytdlp_sleep_interval if job.get("collection") else 0,
                    on_progress, register,
                )
                source, info, cover_src = result.path, result.info, result.cover
                share = DOWNLOAD_SHARE

            self._check_cancel(job_id)
            self._set(job_id, force=True, status="processing", stage="Konvertierung", progress=share,
                      speed=None, eta=None)
            media = self._process(job, opts, source, info, cover_src, workdir, share, register, owner)
            self._set(job_id, force=True, status="done", stage=None, progress=1, media_id=media["id"],
                      finished_at=utcnow(), title=media["title"], channel=media["artist"])
            self.bus.publish({"type": "media", "media": media, "user_id": job.get("user_id")})
            for hook in self.after_done:
                try:
                    hook(job, media)
                except Exception:  # ein Hook-Fehler darf den fertigen Auftrag nicht kippen
                    log.exception("after_done-Hook fehlgeschlagen")
        except UserError as exc:
            cancelled = job_id in self._cancelled
            self._set(job_id, force=True, status="cancelled" if cancelled else "failed", stage=None,
                      error="Abgebrochen." if cancelled else exc.message, finished_at=utcnow(),
                      speed=None, eta=None)
        except Exception as exc:
            log.exception("Job %s fehlgeschlagen", job_id)
            self._set(job_id, force=True, status="failed", stage=None, finished_at=utcnow(),
                      error=f"Interner Fehler: {type(exc).__name__}. Details im Container-Log.")
        finally:
            self._cancelled.discard(job_id)
            self._last_publish.pop(job_id, None)
            shutil.rmtree(workdir, ignore_errors=True)
            if job["kind"] == "upload":
                self._cleanup_upload(job)

    def _cleanup_upload(self, job: dict) -> None:
        opts = job["options"] if isinstance(job["options"], dict) else json.loads(job["options"] or "{}")
        path = opts.get("upload_path")
        if path:
            p = Path(path)
            if p.exists() and self.s.temp_path.resolve() in p.resolve().parents:
                p.unlink(missing_ok=True)

    def _process(self, job: dict, opts: dict, source: Path, info: dict, cover_src: Path | None,
                 workdir: Path, share: float, register, owner: dict | None = None) -> dict:
        job_id = job["id"]
        fmt = job["format"]
        is_audio = fmt in AUDIO_FORMATS
        probe = self.ffmpeg.probe(source)
        file_tags = {k.lower(): v for k, v in (probe.get("format", {}).get("tags") or {}).items()}

        title = (opts.get("title") or info.get("track") or info.get("title") or job.get("title")
                 or file_tags.get("title") or Path(opts.get("original_name") or source.name).stem)
        artist = (opts.get("artist") or info.get("artist") or info.get("channel") or info.get("uploader")
                  or job.get("channel") or file_tags.get("artist"))
        description = opts.get("description") or info.get("description") or file_tags.get("comment")
        upload_date = info.get("upload_date") or ""
        date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}" if len(upload_date) == 8 else None
        collection = job.get("collection") or None
        meta = Metadata(title=str(title)[:300], artist=(str(artist)[:200] if artist else None),
                        album=collection or (str(artist)[:200] if artist else None),
                        description=description, date=date, url=info.get("webpage_url") or None,
                        track=_track(opts.get("track")))

        def on_progress(frac: float) -> None:
            self._set(job_id, progress=round(share + (1 - share) * frac * 0.95, 4))

        out = workdir / f"out.{fmt}"
        if is_audio:
            self._set(job_id, force=True, stage="Audio-Aufbereitung")
            self.ffmpeg.convert_audio(
                source, out, fmt, opts.get("bitrate") or self.s.default_audio_bitrate,
                normalize=bool(opts.get("normalize")), trim_silence=bool(opts.get("trim_silence")),
                on_progress=on_progress, register=register,
            )
        else:
            self._set(job_id, force=True, stage="Video-Aufbereitung")
            self.ffmpeg.convert_video(source, out, fmt, meta, on_progress=on_progress, register=register)
        self._check_cancel(job_id)

        # Cover: Thumbnail von yt-dlp oder eingebettetes Bild der Upload-Datei
        cover: Path | None = None
        cover_input = cover_src
        if cover_input is None and self.ffmpeg.stream(probe, "video") is not None:
            cover_input = source
        if cover_input is not None:
            cover = self.ffmpeg.make_cover(cover_input, workdir / "cover_final.jpg", square=is_audio)

        self._set(job_id, force=True, stage="Metadaten")
        write_tags(out, fmt, meta, cover)

        # Kontingent: erst nach der Konvertierung steht die endgültige Größe fest
        needed = out.stat().st_size + (source.stat().st_size if opts.get("keep_original") else 0)
        remaining = self.users.remaining_bytes(owner) if owner else None
        if remaining is not None and needed > remaining:
            raise UserError(
                f"Nicht genug Speicherkontingent: benötigt {needed // 1048576} MB, frei {remaining // 1048576} MB."
            )

        media_id = uuid.uuid4().hex
        folder = self.users.folder(owner)
        if collection:
            folder = safe_join(folder, sanitize_filename(collection))
        folder.mkdir(parents=True, exist_ok=True)
        dest = unique_path(folder / f"{media_basename(meta.artist, meta.title)}.{fmt}")
        shutil.move(str(out), dest)

        cover_rel = None
        if cover:
            self.s.covers_path.mkdir(parents=True, exist_ok=True)
            cover_dest = self.s.covers_path / f"{media_id}.jpg"
            shutil.move(str(cover), cover_dest)
            cover_rel = str(cover_dest.relative_to(self.s.download_path))

        original_rel = None
        original_size = 0
        if opts.get("keep_original"):
            self.s.originals_path.mkdir(parents=True, exist_ok=True)
            orig_dest = self.s.originals_path / f"{media_id}{source.suffix}"
            shutil.move(str(source), orig_dest)
            original_rel = str(orig_dest.relative_to(self.s.download_path))
            original_size = orig_dest.stat().st_size

        final_probe = self.ffmpeg.probe(dest)
        record = {
            "id": media_id,
            "user_id": job.get("user_id"),
            "job_id": job_id,
            "source_url": None if job["kind"] == "upload" else (info.get("webpage_url") or job["url"]),
            "source_id": info.get("id"),
            "title": meta.title,
            "artist": meta.artist,
            "description": description,
            "duration": self.ffmpeg.duration(final_probe),
            "kind": "audio" if is_audio else "video",
            "format": fmt,
            "path": str(dest.relative_to(self.s.download_path)),
            "original_path": original_rel,
            "original_size": original_size,
            "cover_path": cover_rel,
            "size": dest.stat().st_size,
            "collection": collection,
            "published_at": date,
            "track": meta.track,
            "created_at": utcnow(),
        }
        self.db.insert("media", record)
        return self.library.get(media_id)
