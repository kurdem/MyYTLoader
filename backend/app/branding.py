"""Erscheinungsbild: App-Name und Icon (vom Admin änderbar, ohne Neustart).

Aus einem hochgeladenen Bild erzeugt FFmpeg alle benötigten Größen. Ohne eigenes Icon
werden die mitgelieferten Dateien aus frontend/icons verwendet.
"""
from __future__ import annotations

import html
import json
import logging
import shutil
import threading
import unicodedata
import uuid
from pathlib import Path

from .config import Settings
from .db import Database
from .media import FFmpeg
from .security import UserError

log = logging.getLogger("ytloader.branding")

DEFAULT_NAME = "MyYTLoader"
NAME_KEY = "branding_name"
VERSION_KEY = "branding_version"
MAX_NAME = 40
MAX_ICON_BYTES = 10 * 1024 * 1024
MIN_ICON_PX = 128
MAX_ICON_PX = 8000
BACKGROUND = "0x0f1115"  # Hintergrund für Icons ohne Transparenz (Theme-Farbe)
ALLOWED_CODECS = {"png", "mjpeg", "webp"}

# Dateiname -> (Kantenlänge, Transparenz erlaubt, Rand je Seite als Anteil)
ICON_SPECS: dict[str, tuple[int, bool, float]] = {
    "icon-192.png": (192, True, 0.0),
    "icon-512.png": (512, True, 0.0),
    "icon-maskable-512.png": (512, False, 0.1),  # Android schneidet kreisförmig zu -> Sicherheitsrand
    "apple-touch-icon.png": (180, False, 0.0),   # iOS: kein Alpha, rundet selbst ab
    "podcast-1400.png": (1400, False, 0.0),      # Podcast-Artwork: min. 1400 px, RGB
}


def clean_name(raw: str | None) -> str:
    name = unicodedata.normalize("NFC", raw or "")
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    name = " ".join(name.split())
    if not name:
        raise UserError("Bitte einen Namen eingeben.")
    if len(name) > MAX_NAME:
        raise UserError(f"Der Name darf höchstens {MAX_NAME} Zeichen lang sein.")
    return name


class Branding:
    def __init__(self, settings: Settings, db: Database, ffmpeg: FFmpeg):
        self.s = settings
        self.db = db
        self.ffmpeg = ffmpeg
        self.dir = settings.config_path / "branding"
        self._lock = threading.Lock()

    # ------------------------------------------------------------ Abfragen
    @property
    def name(self) -> str:
        return self.db.kv_get(NAME_KEY) or DEFAULT_NAME

    @property
    def version(self) -> str:
        return self.db.kv_get(VERSION_KEY) or "0"

    @property
    def custom_icon(self) -> bool:
        return (self.dir / "icon-512.png").is_file()

    def file(self, filename: str) -> Path:
        if filename not in ICON_SPECS:
            raise UserError("Nicht gefunden.", status_code=404)
        custom = self.dir / filename
        if custom.is_file():
            return custom
        return self.s.frontend_path / "icons" / filename

    def url(self, filename: str) -> str:
        return f"/branding/{filename}?v={self.version}"

    def info(self) -> dict:
        return {
            "name": self.name,
            "default_name": DEFAULT_NAME,
            "custom_icon": self.custom_icon,
            "version": self.version,
            "icons": {f: self.url(f) for f in ICON_SPECS},
        }

    # ------------------------------------------------------------ Ändern
    def _bump(self) -> None:
        self.db.kv_set(VERSION_KEY, uuid.uuid4().hex[:10])

    def set_name(self, raw: str | None) -> dict:
        name = clean_name(raw)
        if name == DEFAULT_NAME:
            self.db.execute("DELETE FROM kv WHERE key=?", (NAME_KEY,))
        else:
            self.db.kv_set(NAME_KEY, name)
        self._bump()
        return self.info()

    def reset_name(self) -> dict:
        self.db.execute("DELETE FROM kv WHERE key=?", (NAME_KEY,))
        self._bump()
        return self.info()

    def _validate(self, src: Path) -> None:
        if src.stat().st_size > MAX_ICON_BYTES:
            raise UserError(f"Das Bild ist größer als {MAX_ICON_BYTES // 1048576} MB.", status_code=413)
        try:
            probe = self.ffmpeg.probe(src)
        except UserError as exc:
            raise UserError("Die Datei ist kein unterstütztes Bild (PNG, JPG oder WebP).") from exc
        video = self.ffmpeg.stream(probe, "video")
        if not video or video.get("codec_name") not in ALLOWED_CODECS:
            raise UserError("Bitte ein Bild im Format PNG, JPG oder WebP hochladen (SVG wird nicht unterstützt).")
        w, h = int(video.get("width") or 0), int(video.get("height") or 0)
        if min(w, h) < MIN_ICON_PX:
            raise UserError(f"Das Bild ist zu klein ({w}×{h}). Mindestens {MIN_ICON_PX}×{MIN_ICON_PX} px, "
                            "empfohlen 1024×1024 px.")
        if max(w, h) > MAX_ICON_PX:
            raise UserError(f"Das Bild ist zu groß ({w}×{h}). Maximal {MAX_ICON_PX} px Kantenlänge.")

    def _render(self, src: Path, dst: Path, size: int, alpha: bool, pad: float) -> None:
        inner = max(1, round(size * (1 - 2 * pad)))
        square = f"crop='min(iw,ih)':'min(iw,ih)',scale={inner}:{inner}:flags=lanczos,format=rgba"
        if alpha and not pad:
            args = ["-i", str(src), "-vf", square]
        else:
            filt = (f"color=c={BACKGROUND}:s={size}x{size}:d=1,format=rgba[bg];[0:v]{square}[fg];"
                    f"[bg][fg]overlay=(W-w)/2:(H-h)/2:format=auto:shortest=1,format={'rgba' if alpha else 'rgb24'}")
            args = ["-i", str(src), "-filter_complex", filt]
        self.ffmpeg._run(args + ["-frames:v", "1", "-f", "image2", "-c:v", "png", str(dst)])

    def set_icon(self, src: Path) -> dict:
        """Erzeugt alle Icon-Größen aus src und ersetzt die bisherigen atomar."""
        self._validate(src)
        with self._lock:
            work = self.dir.with_name(f".branding-{uuid.uuid4().hex[:8]}")
            work.mkdir(parents=True)
            try:
                for filename, (size, alpha, pad) in ICON_SPECS.items():
                    self._render(src, work / filename, size, alpha, pad)
                old = self.dir.with_name(f".branding-old-{uuid.uuid4().hex[:8]}")
                if self.dir.exists():
                    self.dir.rename(old)
                work.rename(self.dir)
                shutil.rmtree(old, ignore_errors=True)
            except Exception:
                shutil.rmtree(work, ignore_errors=True)
                raise
            self._bump()
        log.info("Neues App-Icon gesetzt")
        return self.info()

    def reset_icon(self) -> dict:
        with self._lock:
            shutil.rmtree(self.dir, ignore_errors=True)
            self._bump()
        return self.info()

    # ------------------------------------------------------------ Ausgabe
    def render_index(self, template: str) -> str:
        return (template
                .replace("%%APP_NAME%%", html.escape(self.name, quote=True))
                .replace("%%BRANDING_V%%", html.escape(self.version, quote=True)))

    def manifest(self) -> str:
        v = self.version
        return json.dumps({
            "name": self.name,
            "short_name": self.name,
            "description": "Selbst gehosteter Media-Downloader und privater Podcast-Server",
            "lang": "de",
            "start_url": "./#/",
            "scope": "./",
            "display": "standalone",
            "orientation": "any",
            "background_color": "#0f1115",
            "theme_color": "#0f1115",
            "icons": [
                {"src": f"branding/icon-192.png?v={v}", "sizes": "192x192", "type": "image/png"},
                {"src": f"branding/icon-512.png?v={v}", "sizes": "512x512", "type": "image/png"},
                {"src": f"branding/icon-maskable-512.png?v={v}", "sizes": "512x512", "type": "image/png",
                 "purpose": "maskable"},
            ],
            "share_target": {
                "action": "./",
                "method": "GET",
                "params": {"url": "url", "text": "text", "title": "title"},
            },
        }, ensure_ascii=False, indent=2)
