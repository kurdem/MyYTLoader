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

from .config import AUDIO_FORMATS, Settings
from .db import Database, job_to_api, utcnow
from .library import Library
from .media import FFmpeg, Metadata, write_tags
from .security import UserError, media_basename, safe_join, sanitize_filename, unique_path
from .ytdlp import Progress, YtDlp, format_selector

log = logging.getLogger("ytloader.jobs")

ACTIVE = ("queued", "running", "processing")
DOWNLOAD_SHARE = 0.85  # Anteil des Downloads am Gesamtfortschritt


class EventBus:
    """Verteilt Ereignisse an alle SSE-Clients (thread-sicher)."""

    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, event: dict) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        try:
            self._loop.call_soon_threadsafe(self._fanout, event)
        except RuntimeError:
            pass

    def _fanout(self, event: dict) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # Client synchronisiert sich beim nächsten Reconnect neu


class JobManager:
    def __init__(self, settings: Settings, db: Database, ytdlp: YtDlp, ffmpeg: FFmpeg,
                 library: Library, bus: EventBus):
        self.s = settings
        self.db = db
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
            row = self.db.one("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at, rowid LIMIT 1")
            if not row:
                return None
            self.db.update("jobs", row["id"], {
                "status": "running", "stage": "Startet", "started_at": utcnow(), "progress": 0, "error": None,
            })
            row["status"] = "running"
            return row

    # ---------------------------------------------------------------- API
    def create(self, *, url: str, fmt: str, options: dict, kind: str = "url", title: str | None = None,
               channel: str | None = None, thumbnail: str | None = None, duration: float | None = None,
               collection: str | None = None) -> dict:
        job = {
            "id": uuid.uuid4().hex,
            "kind": kind,
            "url": url,
            "title": title,
            "channel": channel,
            "thumbnail": thumbnail,
            "duration": duration,
            "format": fmt,
            "options": json.dumps(options),
            "collection": collection,
            "status": "queued",
            "stage": "Wartet",
            "progress": 0,
            "created_at": utcnow(),
        }
        self.db.insert("jobs", job)
        self._publish(job["id"], force=True)
        self.wake()
        return self.get(job["id"])

    def get(self, job_id: str) -> dict:
        row = self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not row:
            raise UserError("Auftrag nicht gefunden.", status_code=404)
        return job_to_api(row)

    def list(self, status: str | None = None, limit: int = 500) -> list[dict]:
        groups = {
            "active": ACTIVE,
            "done": ("done",),
            "failed": ("failed", "cancelled"),
        }
        if status in groups:
            marks = ",".join("?" for _ in groups[status])
            rows = self.db.all(
                f"SELECT * FROM jobs WHERE status IN ({marks}) ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (*groups[status], limit),
            )
        else:
            rows = self.db.all("SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,))
        return [job_to_api(r) for r in rows]

    def cancel(self, job_id: str) -> dict:
        job = self.get(job_id)
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

    def retry(self, job_id: str) -> dict:
        job = self.get(job_id)
        if job["status"] not in ("failed", "cancelled", "done"):
            raise UserError("Dieser Auftrag läuft noch.", 409)
        if job["kind"] != "url":
            raise UserError("Uploads können nicht wiederholt werden. Bitte Datei erneut hochladen.", 409)
        self.db.update("jobs", job_id, {
            "status": "queued", "stage": "Wartet", "progress": 0, "speed": None, "eta": None,
            "error": None, "media_id": None, "started_at": None, "finished_at": None, "created_at": utcnow(),
        })
        self._publish(job_id, force=True)
        self.wake()
        return self.get(job_id)

    def delete(self, job_id: str) -> None:
        job = self.get(job_id)
        if job["status"] in ACTIVE:
            raise UserError("Laufende Aufträge bitte zuerst abbrechen.", 409)
        self.db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        self.bus.publish({"type": "job_deleted", "id": job_id})

    def clear_finished(self) -> int:
        n = self.db.execute("DELETE FROM jobs WHERE status IN ('done','failed','cancelled')")
        self.bus.publish({"type": "jobs_cleared"})
        return n

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
        row = self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if row:
            self.bus.publish({"type": "job", "job": job_to_api(row)})

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
        try:
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
                    job["url"], selector, workdir / "dl", merge, self.s.max_file_size_bytes,
                    self.s.ytdlp_sleep_interval if job.get("collection") else 0,
                    on_progress, register,
                )
                source, info, cover_src = result.path, result.info, result.cover
                share = DOWNLOAD_SHARE

            self._check_cancel(job_id)
            self._set(job_id, force=True, status="processing", stage="Konvertierung", progress=share,
                      speed=None, eta=None)
            media = self._process(job, opts, source, info, cover_src, workdir, share, register)
            self._set(job_id, force=True, status="done", stage=None, progress=1, media_id=media["id"],
                      finished_at=utcnow(), title=media["title"], channel=media["artist"])
            self.bus.publish({"type": "media", "media": media})
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
                 workdir: Path, share: float, register) -> dict:
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
                        description=description, date=date, url=info.get("webpage_url") or None)

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

        media_id = uuid.uuid4().hex
        folder = self.s.download_path
        if collection:
            folder = safe_join(self.s.download_path, sanitize_filename(collection))
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
        if opts.get("keep_original"):
            self.s.originals_path.mkdir(parents=True, exist_ok=True)
            orig_dest = self.s.originals_path / f"{media_id}{source.suffix}"
            shutil.move(str(source), orig_dest)
            original_rel = str(orig_dest.relative_to(self.s.download_path))

        final_probe = self.ffmpeg.probe(dest)
        record = {
            "id": media_id,
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
            "cover_path": cover_rel,
            "size": dest.stat().st_size,
            "collection": collection,
            "published_at": date,
            "created_at": utcnow(),
        }
        self.db.insert("media", record)
        return self.library.get(media_id)
