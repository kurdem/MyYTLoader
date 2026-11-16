"""yt-dlp-Anbindung als Subprozess.

Warum Subprozess statt Python-Import?
- yt-dlp muss häufig aktualisiert werden (YouTube-Änderungen). Ein Update im venv unter
  /config greift so sofort, ohne App-Neustart oder Image-Rebuild.
- Abbrechen = Prozess beenden; Hänger oder Speicherprobleme betreffen nicht die App.
- Aufruf ausschließlich als Argumentliste (keine Shell); "--" trennt die URL von Optionen.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import Settings
from .security import UserError

log = logging.getLogger("ytloader.ytdlp")

PROGRESS_PREFIX = "@@P "
FILE_PREFIX = "@@F "
EXTRACTORS = "youtube,youtube:.*"
_VERSION_RE = re.compile(r"^\d{4}\.\d{1,2}\.\d{1,2}(\.\d+)?$")

# Bekannte Fehlermeldungen -> verständliche Texte (Reihenfolge = Priorität)
_ERROR_MAP: list[tuple[str, str]] = [
    (r"drm", "Dieser Inhalt ist DRM-geschützt und kann nicht heruntergeladen werden."),
    (r"confirm your age|age-restricted|inappropriate for some users",
     "Dieser Inhalt ist altersbeschränkt und erfordert eine Anmeldung. Das wird nicht unterstützt."),
    (r"not a bot|sign in to confirm",
     "YouTube verlangt von diesem Server eine Anmeldung bzw. Bot-Prüfung. Bitte später erneut "
     "versuchen oder yt-dlp unter „Mehr“ aktualisieren. Anmeldedaten werden bewusst nicht unterstützt."),
    (r"private video", "Dieses Video ist privat."),
    (r"members[- ]only|join this channel|available to this channel's members",
     "Dieser Inhalt ist nur für Kanalmitglieder verfügbar und wird nicht unterstützt."),
    (r"premium", "Dieser Inhalt erfordert YouTube Premium und wird nicht unterstützt."),
    (r"live event will begin|premieres in|this live event",
     "Dieser Inhalt ist noch nicht veröffentlicht (Premiere/geplanter Livestream)."),
    (r"is live|livestream|live stream", "Laufende Livestreams werden nicht unterstützt."),
    (r"copyright", "Dieser Inhalt wurde wegen eines Urheberrechtsanspruchs gesperrt."),
    (r"not available in your country|geo.?restrict|blocked it in your country",
     "Dieser Inhalt ist in der Region des Servers nicht verfügbar."),
    (r"requested format is not available",
     "Das gewünschte Format ist für diese Quelle nicht verfügbar."),
    (r"larger than max-filesize|max-filesize",
     "Die Datei überschreitet die konfigurierte maximale Dateigröße."),
    (r"http error 429|too many requests",
     "YouTube hat zu viele Anfragen gemeldet (HTTP 429). Bitte später erneut versuchen."),
    (r"unsupported url", "Diese URL wird nicht unterstützt."),
    (r"no supported javascript runtime|js runtime|javascript runtime",
     "yt-dlp findet keine JavaScript-Runtime (Deno). Bitte das Docker-Image aktualisieren."),
    (r"video unavailable|this video is not available|has been removed|does not exist",
     "Dieses Video ist nicht verfügbar."),
    (r"playlist does not exist|the playlist does not exist", "Diese Playlist existiert nicht."),
    (r"http error 404", "Der Inhalt wurde nicht gefunden (HTTP 404)."),
    (r"http error 403",
     "Die Quelle hat den Zugriff verweigert (HTTP 403). Häufig hilft ein yt-dlp-Update unter „Mehr“."),
    (r"unable to download|timed out|connection|network is unreachable|name or service not known",
     "Netzwerkfehler beim Zugriff auf die Quelle. Bitte später erneut versuchen."),
]


def friendly_error(output: str) -> str:
    error_lines = [l for l in output.splitlines() if l.startswith("ERROR:")]
    haystack = ("\n".join(error_lines) or output).lower()
    for pattern, message in _ERROR_MAP:
        if re.search(pattern, haystack):
            return message
    if error_lines:
        text = error_lines[-1][len("ERROR:"):].strip()
        text = re.sub(r"^\[[^\]]+\]\s*[\w-]+:\s*", "", text)
        return f"Download fehlgeschlagen: {text[:300]}"
    return "Download fehlgeschlagen (unbekannter Fehler)."


def _version_key(v: str | None) -> tuple:
    if not v or not _VERSION_RE.match(v):
        return (0,)
    return tuple(int(p) for p in v.split("."))


@dataclass
class DownloadResult:
    path: Path
    info: dict
    cover: Path | None


@dataclass
class Progress:
    downloaded: int = 0
    total: int | None = None
    speed: float | None = None
    eta: int | None = None
    stream: int = 1
    format_ids: list[str] = field(default_factory=list)


class YtDlp:
    def __init__(self, settings: Settings):
        self.s = settings
        self._update_lock = threading.Lock()
        self._bin_cache: tuple[str, str | None] | None = None

    # ------------------------------------------------------------- Binary/Version
    def _candidates(self) -> list[Path]:
        return [
            self.s.ytdlp_user_venv / "bin" / "yt-dlp",
            self.s.ytdlp_builtin_venv / "bin" / "yt-dlp",
        ]

    @staticmethod
    def _run_version(binary: str) -> str | None:
        try:
            out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        version = out.stdout.strip()
        return version if out.returncode == 0 and version else None

    def resolve(self, refresh: bool = False) -> tuple[str, str | None]:
        """Liefert (Pfad, Version) der neuesten funktionierenden yt-dlp-Installation."""
        if self._bin_cache and not refresh:
            return self._bin_cache
        best: tuple[str, str | None] | None = None
        candidates = [str(p) for p in self._candidates() if p.exists()]
        system = shutil.which("yt-dlp")
        if system:
            candidates.append(system)
        for cand in candidates:
            version = self._run_version(cand)
            if version and (best is None or _version_key(version) > _version_key(best[1])):
                best = (cand, version)
        if best is None:
            raise UserError("yt-dlp ist nicht installiert.", status_code=500)
        self._bin_cache = best
        return best

    def version(self) -> str | None:
        try:
            return self.resolve()[1]
        except UserError:
            return None

    def update(self) -> dict:
        """Installiert/aktualisiert yt-dlp in /config/yt-dlp (persistentes venv)."""
        if not self._update_lock.acquire(blocking=False):
            raise UserError("Ein Update läuft bereits.", status_code=409)
        try:
            before = self.version()
            venv = self.s.ytdlp_user_venv
            py = venv / "bin" / "python"
            if not py.exists() or subprocess.run([str(py), "-c", "pass"], timeout=30).returncode != 0:
                subprocess.run([sys.executable, "-m", "venv", "--clear", str(venv)], check=True, timeout=300)
            cmd = [str(py), "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check",
                   "-U", "yt-dlp[default,deno]"]
            if self.s.ytdlp_channel == "nightly":
                cmd.insert(-1, "--pre")
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if proc.returncode != 0:
                log.error("yt-dlp update failed: %s", proc.stderr[-2000:])
                raise UserError("yt-dlp-Update fehlgeschlagen. Details im Container-Log.", status_code=502)
            after = self.resolve(refresh=True)[1]
            log.info("yt-dlp: %s -> %s", before, after)
            return {"before": before, "after": after, "updated": before != after}
        finally:
            self._update_lock.release()

    def deno_path(self) -> str | None:
        """Deno aus demselben venv wie yt-dlp (Extra "deno"), sonst aus PATH."""
        try:
            binary, _ = self.resolve()
        except UserError:
            binary = None
        if binary:
            candidate = Path(binary).parent / "deno"
            if candidate.exists():
                return str(candidate)
        return shutil.which("deno")

    # ------------------------------------------------------------------- Aufrufe
    def _base_args(self) -> list[str]:
        binary, _ = self.resolve()
        args = [binary]
        deno = self.deno_path()
        if deno:
            args += ["--js-runtimes", f"deno:{deno}"]
        return args + [
            "--ignore-config",
            "--use-extractors", EXTRACTORS,
            "--no-mark-watched",
            "--socket-timeout", "30",
            "--retries", "5",
            "--fragment-retries", "5",
            "--cache-dir", str(self.s.ytdlp_cache_dir),
            "--ffmpeg-location", shutil.which(self.s.ffmpeg_bin) or self.s.ffmpeg_bin,
        ]

    def analyze(self, url: str, max_items: int) -> dict:
        args = self._base_args() + [
            "--dump-single-json", "--skip-download", "--flat-playlist",
            "--playlist-end", str(max_items + 1), "--no-warnings",
        ]
        if "list=" in url and "v=" in url:
            args.append("--no-playlist")
        args += ["--", url]
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired as exc:
            raise UserError("Die Analyse hat zu lange gedauert. Bitte erneut versuchen.", 504) from exc
        if proc.returncode != 0:
            log.warning("analyze failed: %s", proc.stderr[-2000:])
            raise UserError(friendly_error(proc.stderr), status_code=422)
        try:
            info = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise UserError("Unerwartete Antwort von yt-dlp.", status_code=502) from exc
        return summarize_info(info, url, max_items)

    def download(
        self,
        url: str,
        format_selector: str,
        workdir: Path,
        merge_format: str | None,
        max_bytes: int,
        sleep_interval: int,
        on_progress: Callable[[Progress], None],
        register_process: Callable[[subprocess.Popen | None], None],
    ) -> DownloadResult:
        workdir.mkdir(parents=True, exist_ok=True)
        template = (
            "download:" + PROGRESS_PREFIX
            + "%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s"
            + "|%(progress.speed)s|%(progress.eta)s|%(info.format_id)s"
        )
        args = self._base_args() + [
            "--no-playlist",
            "--newline", "--no-colors", "--progress", "--no-quiet",
            "--progress-template", template,
            "--print", "after_move:" + FILE_PREFIX + "%(filepath)s",
            "--format", format_selector,
            "--max-filesize", str(max_bytes),
            "--write-info-json", "--no-write-playlist-metafiles",
            "--write-thumbnail", "--convert-thumbnails", "jpg",
            "--paths", str(workdir),
            "--output", "media.%(ext)s",
            "--output", "infojson:info",
            "--output", "thumbnail:cover.%(ext)s",
        ]
        if merge_format:
            args += ["--merge-output-format", merge_format]
        if sleep_interval > 0:
            args += ["--sleep-interval", str(sleep_interval)]
        args += ["--", url]

        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            cwd=str(workdir),
        )
        register_process(proc)
        tail: list[str] = []
        final_path: Path | None = None
        progress = Progress()
        too_large = False
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.rstrip("\n")
                if line.startswith(PROGRESS_PREFIX):
                    self._parse_progress(line[len(PROGRESS_PREFIX):], progress)
                    total = progress.total or 0
                    if progress.downloaded > max_bytes or total > max_bytes:
                        too_large = True
                        proc.terminate()
                        break
                    on_progress(progress)
                elif line.startswith(FILE_PREFIX):
                    final_path = Path(line[len(FILE_PREFIX):].strip())
                else:
                    tail.append(line)
                    del tail[:-200]
            proc.wait(timeout=60)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            register_process(None)

        output = "\n".join(tail)
        if too_large or "larger than max-filesize" in output:
            raise UserError(
                f"Die Datei überschreitet die maximale Dateigröße von {self.s.max_file_size_mb} MB."
            )
        if proc.returncode not in (0, None) or final_path is None or not final_path.exists():
            if proc.returncode in (-15, -9):
                raise UserError("Abgebrochen.")
            log.warning("download failed (rc=%s): %s", proc.returncode, output[-3000:])
            raise UserError(friendly_error(output))

        info: dict = {}
        info_file = workdir / "info.info.json"
        if info_file.exists():
            try:
                info = json.loads(info_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                info = {}
        cover = workdir / "cover.jpg"
        return DownloadResult(path=final_path, info=info, cover=cover if cover.exists() else None)

    @staticmethod
    def _parse_progress(raw: str, progress: Progress) -> None:
        parts = raw.split("|")
        if len(parts) < 6:
            return

        def num(v: str) -> float | None:
            try:
                return float(v)
            except ValueError:
                return None

        fmt = parts[5]
        if fmt and fmt not in progress.format_ids:
            progress.format_ids.append(fmt)
        progress.stream = max(1, len(progress.format_ids))
        progress.downloaded = int(num(parts[0]) or 0)
        total = num(parts[1]) or num(parts[2])
        progress.total = int(total) if total else None
        progress.speed = num(parts[3])
        eta = num(parts[4])
        progress.eta = int(eta) if eta is not None else None


# ------------------------------------------------------------------- Analyse


def _best_thumbnail(info: dict) -> str | None:
    thumbs = [t for t in info.get("thumbnails") or [] if t.get("url")]
    if thumbs:
        thumbs.sort(key=lambda t: (t.get("preference") or 0, (t.get("width") or 0)))
        for t in reversed(thumbs):
            if t["url"].startswith("https://") and not t["url"].endswith(".webp"):
                return t["url"]
        return thumbs[-1]["url"]
    thumb = info.get("thumbnail")
    return thumb if isinstance(thumb, str) else None


_LOSSLESS = ("flac", "alac", "pcm", "wav")


def summarize_info(info: dict, url: str, max_items: int) -> dict:
    if info.get("_type") == "playlist":
        entries = []
        for e in info.get("entries") or []:
            if not e or not e.get("id"):
                continue
            if e.get("ie_key") not in (None, "Youtube"):
                continue  # z. B. verschachtelte Tabs/Kanäle überspringen
            entries.append({
                "id": e["id"],
                "title": e.get("title") or e["id"],
                "duration": e.get("duration"),
                "channel": e.get("channel") or e.get("uploader"),
                "thumbnail": _best_thumbnail(e),
            })
        truncated = len(entries) > max_items
        entries = entries[:max_items]
        if not entries:
            raise UserError("Die Playlist enthält keine herunterladbaren Videos.", status_code=422)
        return {
            "type": "playlist",
            "id": info.get("id"),
            "url": info.get("webpage_url") or url,
            "title": info.get("title") or "Playlist",
            "channel": info.get("channel") or info.get("uploader"),
            "description": (info.get("description") or "")[:5000],
            "thumbnail": _best_thumbnail(info) or (entries[0]["thumbnail"] if entries else None),
            "entries": entries,
            "truncated": truncated,
            "max_items": max_items,
        }

    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise UserError("Laufende oder geplante Livestreams werden nicht unterstützt.", status_code=422)
    formats = info.get("formats") or []
    if formats and all(f.get("has_drm") for f in formats):
        raise UserError("Dieser Inhalt ist DRM-geschützt und kann nicht heruntergeladen werden.", 422)
    audio = [f for f in formats if f.get("acodec") not in (None, "none") and not f.get("has_drm")]
    video = [f for f in formats if f.get("vcodec") not in (None, "none") and not f.get("has_drm")]
    heights = sorted({int(f["height"]) for f in video if f.get("height")}, reverse=True)
    abrs = [f.get("abr") for f in audio if f.get("abr")]
    lossless = any(str(f.get("acodec", "")).lower().startswith(_LOSSLESS) for f in audio)
    playlist_url = None
    if "list=" in url:
        m = re.search(r"[?&]list=([A-Za-z0-9_-]+)", url)
        if m:
            playlist_url = f"https://www.youtube.com/playlist?list={m.group(1)}"
    return {
        "type": "video",
        "id": info.get("id"),
        "url": info.get("webpage_url") or url,
        "title": info.get("title") or info.get("id") or "Unbenannt",
        "channel": info.get("channel") or info.get("uploader"),
        "description": (info.get("description") or "")[:5000],
        "duration": info.get("duration"),
        "thumbnail": _best_thumbnail(info),
        "upload_date": info.get("upload_date"),
        "audio": {
            "best_abr": round(max(abrs)) if abrs else None,
            "codecs": sorted({str(f.get("acodec")).split(".")[0] for f in audio}),
            "lossless": lossless,
        },
        "video_heights": heights,
        "playlist_url": playlist_url,
    }


def format_selector(fmt: str, max_height: int | None) -> tuple[str, str | None]:
    """Liefert (yt-dlp-Formatselektor, Merge-Container)."""
    h = f"[height<={int(max_height)}]" if max_height else ""
    if fmt == "flac":
        # Nur echte verlustfreie Quellen – kein "Hochkonvertieren" verlustbehafteter Spuren.
        return "ba[acodec~='^(flac|alac|pcm)']", None
    if fmt in ("m4a", "mp3", "opus"):
        return "ba/b", None
    if fmt == "mp4":
        return (
            f"bv*[vcodec^=avc1]{h}+ba[acodec^=mp4a]/b[ext=mp4][vcodec^=avc1]{h}"
            f"/bv*{h}+ba/b{h}/bv*+ba/b",
            "mp4",
        )
    if fmt == "webm":
        return f"bv*[ext=webm]{h}+ba[ext=webm]/b[ext=webm]{h}/bv*[ext=webm]+ba[ext=webm]", "webm"
    raise UserError("Unbekanntes Format.")
