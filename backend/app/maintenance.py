"""Periodische Wartung: Temp-Bereinigung, Aufbewahrungsfrist, yt-dlp-Updates."""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time

from .db import utcnow

log = logging.getLogger("ytloader.maintenance")

CLEANUP_INTERVAL = 3600
YTDLP_UPDATE_INTERVAL = 24 * 3600


def _protected_paths(svc) -> set[str]:
    """Temp-Pfade aktiver Jobs (Arbeitsordner + Upload-Dateien)."""
    protected: set[str] = set()
    for row in svc.db.all("SELECT id, options FROM jobs WHERE status IN ('queued','running','processing')"):
        protected.add(str(svc.settings.temp_path / "jobs" / row["id"]))
        try:
            up = json.loads(row["options"] or "{}").get("upload_path")
        except json.JSONDecodeError:
            up = None
        if up:
            protected.add(up)
    return protected


def cleanup_temp(svc, max_age_hours: int | None = None) -> int:
    age = svc.settings.temp_max_age_hours if max_age_hours is None else max_age_hours
    cutoff = time.time() - age * 3600
    protected = _protected_paths(svc)
    deleted = 0
    for sub in ("jobs", "uploads"):
        folder = svc.settings.temp_path / sub
        if not folder.is_dir():
            continue
        for entry in folder.iterdir():
            if str(entry) in protected:
                continue
            try:
                if entry.stat().st_mtime > cutoff:
                    continue
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
                deleted += 1
            except OSError:
                continue
    if deleted:
        log.info("Temp-Bereinigung: %d Einträge entfernt", deleted)
    return deleted


def update_ytdlp(svc) -> None:
    try:
        result = svc.ytdlp.update()
        svc.db.kv_set("ytdlp_last_update", utcnow())
        if result.get("updated"):
            log.info("yt-dlp aktualisiert: %s -> %s", result["before"], result["after"])
    except Exception as exc:  # Netzwerk weg o. ä. – eingebaute Version bleibt nutzbar
        log.warning("Automatisches yt-dlp-Update fehlgeschlagen: %s", exc)


SUBSCRIPTION_TICK = 60


async def subscriptions_loop(svc) -> None:
    """Prüft fällige Abos nacheinander (eine Prüfung gleichzeitig, schont YouTube)."""
    await asyncio.sleep(20)  # Start nicht verzögern
    while True:
        try:
            for sub_id in await asyncio.to_thread(svc.subscriptions.due):
                try:
                    await asyncio.to_thread(svc.subscriptions.check, sub_id)
                except Exception as exc:  # z. B. "läuft bereits" durch manuelle Prüfung
                    log.info("Abo-Prüfung %s übersprungen: %s", sub_id, exc)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Abo-Prüfung fehlgeschlagen")
        await asyncio.sleep(SUBSCRIPTION_TICK)


async def maintenance_loop(svc) -> None:
    last_update = 0.0
    while True:
        try:
            await asyncio.to_thread(cleanup_temp, svc)
            if svc.settings.retention_days > 0:
                n = await asyncio.to_thread(svc.library.delete_older_than, svc.settings.retention_days)
                if n:
                    log.info("Aufbewahrungsfrist: %d Medien gelöscht", n)
            if svc.settings.ytdlp_auto_update and time.time() - last_update > YTDLP_UPDATE_INTERVAL:
                last_update = time.time()
                await asyncio.to_thread(update_ytdlp, svc)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Wartung fehlgeschlagen")
        await asyncio.sleep(CLEANUP_INTERVAL)
