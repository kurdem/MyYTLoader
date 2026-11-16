"""Login per signiertem Session-Cookie, Secret-/Passwort-Handling, CSRF-Header."""
from __future__ import annotations

import hmac
import logging
import os
import secrets
from pathlib import Path

from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import Settings
from .security import UserError

log = logging.getLogger("ytloader.auth")

COOKIE_NAME = "ytl_session"
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "ytloader"


def _read_or_create(path: Path, generator) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = generator()
    path.write_text(value + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return value


class Auth:
    def __init__(self, settings: Settings):
        self.s = settings
        self.enabled = settings.auth_enabled
        self.username = settings.admin_username
        secret = settings.secret_key or _read_or_create(
            settings.config_path / "secret.key", lambda: secrets.token_urlsafe(48)
        )
        self._serializer = URLSafeTimedSerializer(secret, salt="ytloader-session")
        self.max_age = settings.session_max_age_days * 86400
        self._password = settings.admin_password
        if self.enabled and not self._password:
            pw_file = settings.config_path / "admin_password.txt"
            existed = pw_file.exists()
            self._password = _read_or_create(pw_file, lambda: secrets.token_urlsafe(12))
            if not existed:
                log.warning(
                    "Kein ADMIN_PASSWORD gesetzt. Generiertes Passwort für '%s': %s "
                    "(gespeichert in %s)", self.username, self._password, pw_file,
                )

    def check_credentials(self, username: str, password: str) -> bool:
        user_ok = hmac.compare_digest(username.encode(), self.username.encode())
        pw_ok = hmac.compare_digest(password.encode(), self._password.encode())
        return user_ok and pw_ok

    def issue(self, username: str) -> str:
        return self._serializer.dumps({"u": username})

    def session_user(self, request: Request) -> str | None:
        if not self.enabled:
            return "anonymous"
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return None
        try:
            data = self._serializer.loads(token, max_age=self.max_age)
        except (BadSignature, SignatureExpired):
            return None
        user = data.get("u") if isinstance(data, dict) else None
        return user if user == self.username else None

    def require(self, request: Request) -> str:
        user = self.session_user(request)
        if not user:
            raise UserError("Bitte anmelden.", status_code=401)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            # Schutz gegen Cross-Site-Requests (zusätzlich zu SameSite=Lax)
            if request.headers.get(CSRF_HEADER, "").lower() != CSRF_VALUE:
                raise UserError("Ungültige Anfrage (CSRF-Schutz).", status_code=403)
        return user


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def is_https(request: Request) -> bool:
    return request.url.scheme == "https"
