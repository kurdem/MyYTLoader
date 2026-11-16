"""Anmeldung per signiertem Session-Cookie, Rollen, CSRF-Header."""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import Settings
from .security import UserError
from .users import Users

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
    def __init__(self, settings: Settings, users: Users):
        self.s = settings
        self.users = users
        self.enabled = settings.auth_enabled
        secret = settings.secret_key or _read_or_create(
            settings.config_path / "secret.key", lambda: secrets.token_urlsafe(48)
        )
        self._serializer = URLSafeTimedSerializer(secret, salt="ytloader-session")
        self.max_age = settings.session_max_age_days * 86400
        self.admin = self._bootstrap()

    def _bootstrap(self) -> dict:
        password = self.s.admin_password
        if not password:
            pw_file = self.s.config_path / "admin_password.txt"
            existed = pw_file.exists()
            password = _read_or_create(pw_file, lambda: secrets.token_urlsafe(12))
            if not existed and self.enabled:
                log.warning("Kein ADMIN_PASSWORD gesetzt. Generiertes Passwort für '%s': %s (gespeichert in %s)",
                            self.s.admin_username, password, pw_file)
        admin = self.users.bootstrap(password)
        if self.s.admin_password_reset and self.s.admin_password:
            self.users.reset_admin_password(self.s.admin_password)
            admin = self.users.require(admin["id"])
        return admin

    def issue(self, user: dict) -> str:
        return self._serializer.dumps({"u": user["id"], "v": user["session_version"]})

    def session_user(self, request: Request) -> dict | None:
        if not self.enabled:
            # Einzelbenutzer-Modus ohne Login: alles gehört dem Admin
            return self.users.get(self.admin["id"])
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return None
        try:
            data = self._serializer.loads(token, max_age=self.max_age)
        except (BadSignature, SignatureExpired):
            return None
        if not isinstance(data, dict):
            return None
        user = self.users.get(data.get("u"))
        if not user or user["disabled"] or user["session_version"] != data.get("v"):
            return None
        return user

    def require(self, request: Request) -> dict:
        user = self.session_user(request)
        if not user:
            raise UserError("Bitte anmelden.", status_code=401)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            # Schutz gegen Cross-Site-Requests (zusätzlich zu SameSite=Lax)
            if request.headers.get(CSRF_HEADER, "").lower() != CSRF_VALUE:
                raise UserError("Ungültige Anfrage (CSRF-Schutz).", status_code=403)
        return user


def is_admin(user: dict | None) -> bool:
    return bool(user and user.get("role") == "admin")


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def is_https(request: Request) -> bool:
    return request.url.scheme == "https"
