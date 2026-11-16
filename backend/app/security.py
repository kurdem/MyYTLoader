"""URL-Validierung (SSRF-Schutz), Dateinamen/Pfade und Rate Limiting."""
from __future__ import annotations

import ipaddress
import re
import socket
import threading
import time
import unicodedata
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


class UserError(Exception):
    """Fehler mit verständlicher Meldung für die Oberfläche."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# --------------------------------------------------------------------------- URLs

_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def host_allowed(host: str, allowed: tuple[str, ...]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in allowed)


def _resolve(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({info[4][0] for info in infos})


def validate_url(raw: str, allowed: tuple[str, ...], check_dns: bool = True, resolver=_resolve) -> str:
    """Prüft eine vom Benutzer eingegebene URL und liefert eine normalisierte Fassung.

    - nur http(s), keine Zugangsdaten, keine fremden Ports
    - Host muss auf der Allowlist stehen (inkl. Subdomains)
    - alle aufgelösten Adressen müssen öffentlich sein (kein Zugriff auf interne Netze)
    """
    if not isinstance(raw, str):
        raise UserError("Bitte eine gültige URL eingeben.")
    url = raw.strip()
    if not url:
        raise UserError("Bitte eine URL eingeben.")
    if len(url) > 2048:
        raise UserError("Die URL ist zu lang.")
    if any(c.isspace() or ord(c) < 32 for c in url):
        raise UserError("Die URL enthält ungültige Zeichen.")
    if "://" not in url:
        url = "https://" + url
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UserError("Die URL ist ungültig.") from exc
    if parts.scheme.lower() not in ("http", "https"):
        raise UserError("Nur http- und https-Links werden unterstützt.")
    if parts.username or parts.password:
        raise UserError("Links mit Zugangsdaten werden nicht unterstützt.")
    host = (parts.hostname or "").lower()
    if not host:
        raise UserError("Die URL enthält keinen Host.")
    if port not in (None, 80, 443):
        raise UserError("Links mit abweichendem Port werden nicht unterstützt.")
    try:
        ipaddress.ip_address(host)
        raise UserError("IP-Adressen werden nicht unterstützt. Bitte einen YouTube-Link verwenden.")
    except ValueError:
        pass
    if not host_allowed(host, allowed):
        raise UserError(
            "Diese Quelle wird nicht unterstützt. Erlaubt sind: " + ", ".join(allowed) + "."
        )
    if check_dns:
        try:
            addresses = resolver(host)
        except OSError as exc:
            raise UserError(f"Der Host {host} konnte nicht aufgelöst werden.") from exc
        if not addresses:
            raise UserError(f"Der Host {host} konnte nicht aufgelöst werden.")
        for addr in addresses:
            ip = ipaddress.ip_address(addr.split("%")[0])
            if not ip.is_global or ip.is_multicast:
                raise UserError("Der Host verweist auf eine interne Adresse und ist gesperrt.")
    return urlunsplit(("https", host, parts.path or "/", parts.query, ""))


def youtube_video_url(video_id: str) -> str:
    if not isinstance(video_id, str) or not _YT_ID.match(video_id):
        raise UserError("Ungültige Video-ID in der Auswahl.")
    return f"https://www.youtube.com/watch?v={video_id}"


# ---------------------------------------------------------------------- Dateinamen

_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str | None, max_bytes: int = 180, fallback: str = "Unbenannt") -> str:
    """Erzeugt einen plattformübergreifend sicheren Dateinamen (ohne Endung)."""
    name = unicodedata.normalize("NFC", name or "")
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    name = name.replace(":", " -").replace("/", "-").replace("\\", "-").replace("|", "-")
    name = _FORBIDDEN.sub("", name)
    name = re.sub(r"\s+", " ", name).strip(" .-_")
    while len(name.encode("utf-8")) > max_bytes:
        name = name[:-1]
    name = name.rstrip(" .-_")
    if not name or name.lower() in _RESERVED:
        return fallback
    return name


def media_basename(artist: str | None, title: str | None) -> str:
    title = sanitize_filename(title, max_bytes=140)
    artist = sanitize_filename(artist, max_bytes=60, fallback="")
    if artist and not title.lower().startswith(artist.lower()):
        return f"{artist} - {title}"
    return title


def safe_join(base: Path, *parts: str) -> Path:
    """Verbindet Pfade und stellt sicher, dass das Ergebnis innerhalb von base liegt."""
    base_resolved = base.resolve()
    target = base_resolved.joinpath(*parts).resolve()
    if target != base_resolved and base_resolved not in target.parents:
        raise UserError("Ungültiger Pfad.", status_code=400)
    return target


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(2, 10_000):
        candidate = path.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
    raise UserError("Kein freier Dateiname gefunden.")


# --------------------------------------------------------------------- Rate Limit


class RateLimiter:
    """Gleitendes Zeitfenster pro Schlüssel (z. B. Client-IP)."""

    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                retry = int(self.window - (now - hits[0])) + 1
                raise UserError(
                    f"Zu viele Anfragen. Bitte in {retry} Sekunden erneut versuchen.", status_code=429
                )
            hits.append(now)
            if len(self._hits) > 10_000:  # Speicher begrenzen
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]
