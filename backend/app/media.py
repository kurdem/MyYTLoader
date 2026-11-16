"""Medienverarbeitung mit FFmpeg (Konvertierung, Lautheit, Stille) und Tagging mit mutagen."""
from __future__ import annotations

import base64
import json
import logging
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .security import UserError

log = logging.getLogger("ytloader.media")

# Podcast-übliche Lautheit (Apple empfiehlt -16 LUFS), Spitzen max. -1,5 dBFS
TARGET_LUFS = -16.0
PEAK_LIMIT_DB = -1.5
MAX_GAIN_DB = 20.0  # sehr leise Quellen nicht ins Rauschen hochziehen
ANALYSIS_SHARE = 0.2  # Anteil der Analyse am Fortschritt der Aufbereitung
SILENCE_THRESHOLD_DB = -50
SILENCE_MIN_SECONDS = 2.0
SILENCE_KEEP_SECONDS = 0.3

AUDIO_CODECS = {
    "m4a": ["-c:a", "aac"],
    "mp3": ["-c:a", "libmp3lame"],
    "opus": ["-c:a", "libopus", "-vbr", "on"],
    "flac": ["-c:a", "flac", "-compression_level", "8"],
}
SAMPLE_RATES = {"m4a": "44100", "mp3": "44100", "opus": "48000", "flac": "48000"}
SOURCE_CODEC_MATCH = {"m4a": "aac", "mp3": "mp3", "opus": "opus", "flac": "flac"}
MIME_TYPES = {
    "m4a": "audio/mp4",
    "mp3": "audio/mpeg",
    "opus": "audio/ogg",
    "flac": "audio/flac",
    "mp4": "video/mp4",
    "webm": "video/webm",
}


@dataclass
class Metadata:
    title: str
    artist: str | None = None
    album: str | None = None
    description: str | None = None
    date: str | None = None  # YYYY oder YYYY-MM-DD
    url: str | None = None


class FFmpeg:
    def __init__(self, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe"):
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    # ----------------------------------------------------------------- Basis
    def version(self) -> str | None:
        try:
            out = subprocess.run([self.ffmpeg, "-version"], capture_output=True, text=True, timeout=15)
            first = out.stdout.splitlines()[0] if out.stdout else ""
            m = re.search(r"version (\S+)", first)
            return m.group(1) if m else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    def probe(self, path: Path) -> dict:
        proc = subprocess.run(
            [self.ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0:
            raise UserError("Die Datei ist keine gültige Mediendatei.")
        return json.loads(proc.stdout or "{}")

    @staticmethod
    def duration(probe: dict) -> float | None:
        try:
            return round(float(probe.get("format", {}).get("duration")), 2)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def stream(probe: dict, kind: str) -> dict | None:
        for s in probe.get("streams", []):
            if s.get("codec_type") == kind and not (s.get("disposition") or {}).get("attached_pic"):
                return s
        return None

    def _run(self, args: list[str], duration: float | None = None,
             on_progress: Callable[[float], None] | None = None, register=None) -> str:
        cmd = [self.ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "info", "-progress", "pipe:1", *args]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if register:
            register(proc)
        stderr_chunks: list[str] = []

        def drain():
            assert proc.stderr is not None
            for line in proc.stderr:
                stderr_chunks.append(line)
                if len(stderr_chunks) > 4000:
                    del stderr_chunks[:2000]

        t = threading.Thread(target=drain, daemon=True)
        t.start()
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if on_progress and duration and line.startswith("out_time_us="):
                    try:
                        us = int(line.split("=", 1)[1])
                        on_progress(max(0.0, min(1.0, us / 1_000_000 / duration)))
                    except ValueError:
                        pass
            proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            if register:
                register(None)
            t.join(timeout=5)
        stderr = "".join(stderr_chunks)
        if proc.returncode != 0:
            if proc.returncode in (-15, -9):
                raise UserError("Abgebrochen.")
            log.error("ffmpeg failed: %s\n%s", " ".join(cmd), stderr[-3000:])
            raise UserError("Die Konvertierung mit FFmpeg ist fehlgeschlagen.")
        return stderr

    # ------------------------------------------------------------- Audio
    def analyze_audio(self, src: Path, duration: float | None = None, on_progress=None, register=None) -> dict:
        """Ein schneller Analyse-Durchlauf: Lautheit (EBU R128) + Stille am Anfang/Ende.

        ebur128 ist deutlich schneller als loudnorm (das intern auf 192 kHz hochrechnet).
        """
        filt = (
            "ebur128=peak=sample:framelog=quiet,"
            f"silencedetect=noise={SILENCE_THRESHOLD_DB}dB:d={SILENCE_MIN_SECONDS}"
        )
        stderr = self._run(["-i", str(src), "-vn", "-sn", "-dn", "-af", filt, "-f", "null", "-"],
                           duration=duration, on_progress=on_progress, register=register)
        result: dict = {"loudness": None, "silences": []}
        summary = stderr[stderr.rfind("Summary:"):] if "Summary:" in stderr else ""
        m_i = re.search(r"I:\s+(-?[\d.]+|-inf) LUFS", summary)
        m_p = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summary)
        if m_i and m_i.group(1) != "-inf":
            peak = float(m_p.group(1)) if m_p and m_p.group(1) != "-inf" else None
            result["loudness"] = {"I": float(m_i.group(1)), "peak": peak}
        starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", stderr)]
        ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", stderr)]
        for i, start in enumerate(starts):
            result["silences"].append((max(0.0, start), ends[i] if i < len(ends) else None))
        return result

    @staticmethod
    def gain_filters(loudness: dict | None) -> list[str]:
        """Pegelanpassung auf TARGET_LUFS plus Limiter gegen Übersteuerung."""
        if not loudness:
            return []
        gain = max(-MAX_GAIN_DB, min(MAX_GAIN_DB, TARGET_LUFS - loudness["I"]))
        filters = [f"volume={gain:.2f}dB"]
        peak = loudness.get("peak")
        if peak is None or peak + gain > PEAK_LIMIT_DB:
            limit = 10 ** (PEAK_LIMIT_DB / 20)
            filters.append(f"alimiter=limit={limit:.4f}:level=false")
        return filters

    @staticmethod
    def trim_window(silences: list[tuple[float, float | None]], duration: float | None) -> tuple[float, float | None]:
        """Berechnet Start/Ende ohne längere Stille am Anfang und Ende."""
        start, end = 0.0, None
        if silences:
            first_start, first_end = silences[0]
            if first_start <= 0.05 and first_end is not None:
                start = max(0.0, first_end - SILENCE_KEEP_SECONDS)
            last_start, last_end = silences[-1]
            if last_end is None or (duration and duration - last_end <= 0.1):
                if last_start > start:
                    end = last_start + SILENCE_KEEP_SECONDS
        return start, end

    def convert_audio(
        self, src: Path, dst: Path, fmt: str, bitrate: str, normalize: bool, trim_silence: bool,
        on_progress=None, register=None,
    ) -> None:
        probe = self.probe(src)
        src_audio = self.stream(probe, "audio")
        if not src_audio:
            raise UserError("Die Quelle enthält keine Audiospur.")
        duration = self.duration(probe)
        filters: list[str] = []
        encode_progress = on_progress
        if normalize or trim_silence:
            analysis = self.analyze_audio(
                src, duration=duration, register=register,
                on_progress=(lambda f: on_progress(f * ANALYSIS_SHARE)) if on_progress else None,
            )
            if on_progress:
                encode_progress = lambda f: on_progress(ANALYSIS_SHARE + (1 - ANALYSIS_SHARE) * f)  # noqa: E731
            if trim_silence:
                start, end = self.trim_window(analysis["silences"], duration)
                if start > 0 or end:
                    filters.append(f"atrim=start={start:.3f}" + (f":end={end:.3f}" if end else ""))
                    filters.append("asetpts=PTS-STARTPTS")
            if normalize:
                filters += self.gain_filters(analysis.get("loudness"))
        args = ["-i", str(src), "-map", "0:a:0", "-vn", "-sn", "-dn", "-map_metadata", "-1"]
        same_codec = (src_audio.get("codec_name") == SOURCE_CODEC_MATCH[fmt])
        if not filters and same_codec and fmt != "flac":
            args += ["-c:a", "copy"]  # kein unnötiges Neukodieren
        else:
            if filters:
                filters.append(f"aresample={SAMPLE_RATES[fmt]}")
                args += ["-af", ",".join(filters)]
            elif fmt == "opus":
                args += ["-ar", "48000"]
            args += AUDIO_CODECS[fmt]
            if fmt != "flac":
                args += ["-b:a", bitrate]
        if fmt == "m4a":
            args += ["-movflags", "+faststart", "-f", "ipod"]
        elif fmt == "mp3":
            args += ["-f", "mp3"]
        elif fmt == "opus":
            args += ["-f", "opus"]
        elif fmt == "flac":
            args += ["-f", "flac"]
        args.append(str(dst))
        self._run(args, duration=duration, on_progress=encode_progress, register=register)

    # ------------------------------------------------------------- Video
    def convert_video(self, src: Path, dst: Path, fmt: str, meta: Metadata | None = None,
                      on_progress=None, register=None) -> None:
        probe = self.probe(src)
        v = self.stream(probe, "video")
        a = self.stream(probe, "audio")
        if not v:
            raise UserError("Die Quelle enthält keine Videospur.")
        duration = self.duration(probe)
        args = ["-i", str(src), "-map", "0:v:0"]
        if a:
            args += ["-map", "0:a:0"]
        args += ["-sn", "-dn", "-map_metadata", "-1"]
        if fmt == "mp4":
            if v.get("codec_name") == "h264":
                args += ["-c:v", "copy"]
            else:  # iOS-kompatibel neu kodieren
                args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                         "-profile:v", "high"]
            args += ["-tag:v", "avc1"]
            if a:
                args += ["-c:a", "copy"] if a.get("codec_name") == "aac" else ["-c:a", "aac", "-b:a", "192k"]
            args += ["-movflags", "+faststart", "-f", "mp4"]
        else:  # webm
            args += ["-c:v", "copy"]
            if a:
                args += ["-c:a", "copy"] if a.get("codec_name") in ("opus", "vorbis") else ["-c:a", "libopus", "-b:a", "160k"]
            if meta:  # mutagen kann kein Matroska -> Metadaten direkt setzen
                for key, value in (("title", meta.title), ("artist", meta.artist),
                                   ("album", meta.album), ("comment", meta.description), ("date", meta.date)):
                    if value:
                        args += ["-metadata", f"{key}={value[:2000]}"]
            args += ["-f", "webm"]
        args.append(str(dst))
        self._run(args, duration=duration, on_progress=on_progress, register=register)

    # ------------------------------------------------------------- Cover
    def make_cover(self, src: Path, dst: Path, square: bool = True) -> Path | None:
        vf = "crop='min(iw,ih)':'min(iw,ih)'," if square else ""
        vf += "scale='min(1400,iw)':-2"
        try:
            self._run(["-i", str(src), "-frames:v", "1", "-vf", vf, "-q:v", "3", "-f", "image2", str(dst)])
        except UserError:
            return None
        return dst if dst.exists() else None


# ------------------------------------------------------------------ Tagging


def write_tags(path: Path, fmt: str, meta: Metadata, cover: Path | None) -> None:
    """Schreibt ID3-/MP4-/Vorbis-Tags inkl. Cover (WebM: über FFmpeg erledigt)."""
    if fmt == "webm":
        return
    cover_data = cover.read_bytes() if cover and cover.exists() else None
    desc = (meta.description or "")[:4000] or None
    year = (meta.date or "")[:10] or None
    try:
        if fmt == "mp3":
            from mutagen.id3 import APIC, COMM, ID3, TALB, TDRC, TIT2, TPE1, WOAS, ID3NoHeaderError

            try:
                tags = ID3(str(path))
            except ID3NoHeaderError:
                tags = ID3()
            tags.delall("APIC")
            tags.add(TIT2(encoding=3, text=meta.title))
            if meta.artist:
                tags.add(TPE1(encoding=3, text=meta.artist))
            if meta.album:
                tags.add(TALB(encoding=3, text=meta.album))
            if desc:
                tags.add(COMM(encoding=3, lang="deu", desc="", text=desc))
            if year:
                tags.add(TDRC(encoding=3, text=year))
            if meta.url:
                tags.add(WOAS(url=meta.url))
            if cover_data:
                tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover_data))
            tags.save(str(path), v2_version=3)
        elif fmt in ("m4a", "mp4"):
            from mutagen.mp4 import MP4, MP4Cover

            f = MP4(str(path))
            if f.tags is None:
                f.add_tags()
            t = f.tags
            t["\xa9nam"] = [meta.title]
            if meta.artist:
                t["\xa9ART"] = [meta.artist]
                t["aART"] = [meta.artist]
            if meta.album:
                t["\xa9alb"] = [meta.album]
            if desc:
                t["desc"] = [desc[:255]]
                t["ldes"] = [desc]
                t["\xa9cmt"] = [desc[:255]]
            if year:
                t["\xa9day"] = [year]
            if cover_data:
                t["covr"] = [MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)]
            f.save()
        elif fmt in ("opus", "flac"):
            from mutagen.flac import FLAC, Picture
            from mutagen.oggopus import OggOpus

            f = OggOpus(str(path)) if fmt == "opus" else FLAC(str(path))
            f["title"] = meta.title
            for key, value in (("artist", meta.artist), ("album", meta.album),
                               ("description", desc), ("comment", desc), ("date", year)):
                if value:
                    f[key] = value
            if cover_data:
                pic = Picture()
                pic.type = 3
                pic.mime = "image/jpeg"
                pic.desc = "Cover"
                pic.data = cover_data
                if fmt == "flac":
                    f.clear_pictures()
                    f.add_picture(pic)
                else:
                    f["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
            f.save()
    except Exception as exc:  # Tags sind "nice to have" – Datei trotzdem behalten
        log.warning("Tagging fehlgeschlagen für %s: %s", path.name, exc)
