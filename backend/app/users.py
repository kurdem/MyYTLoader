"""Benutzerverwaltung: Konten, Rollen, Passwörter (scrypt), Kontingente, Feed-Tokens."""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import uuid
from pathlib import Path

from .config import Settings
from .db import Database, utcnow
from .security import UserError

log = logging.getLogger("ytloader.users")

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
ROLES = ("admin", "user")
MIN_PASSWORD = 8
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}
LEGACY_FEED_KEY = "feed_token"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return "scrypt${n}${r}${p}${salt}${hash}".format(
        **_SCRYPT, salt=base64.b64encode(salt).decode(), hash=base64.b64encode(digest).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r),
                              p=int(p), dklen=len(base64.b64decode(digest)))
        return hmac.compare_digest(calc, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def _limit(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise UserError("Ungültiger Zahlenwert.") from exc
    if value < 0:
        raise UserError("Werte dürfen nicht negativ sein.")
    return value or None


def public(user: dict) -> dict:
    """Benutzer ohne Geheimnisse (Hash, Feed-Token, Session-Version)."""
    return {k: v for k, v in user.items() if k not in ("password_hash", "feed_token", "session_version")}


class Users:
    def __init__(self, settings: Settings, db: Database):
        self.s = settings
        self.db = db
        # Dummy-Hash für gleichmäßige Laufzeit bei unbekannten Benutzern
        self._dummy_hash = hash_password(secrets.token_hex(8))

    # ------------------------------------------------------------- Abfragen
    def get(self, user_id: str | None) -> dict | None:
        return self.db.one("SELECT * FROM users WHERE id=?", (user_id,)) if user_id else None

    def require(self, user_id: str) -> dict:
        user = self.get(user_id)
        if not user:
            raise UserError("Benutzer nicht gefunden.", status_code=404)
        return user

    def by_username(self, username: str) -> dict | None:
        return self.db.one("SELECT * FROM users WHERE username=?", (username.strip().lower(),))

    def by_feed_token(self, token: str) -> dict | None:
        if not token or len(token) > 100:
            return None
        user = self.db.one("SELECT * FROM users WHERE feed_token=? AND disabled=0", (token,))
        if user and hmac.compare_digest(user["feed_token"].encode(), token.encode()):
            return user
        return None

    def list(self) -> list[dict]:
        rows = self.db.all(
            "SELECT u.*, COALESCE(SUM(m.size + m.original_size), 0) AS usage_bytes, COUNT(m.id) AS media_count "
            "FROM users u LEFT JOIN media m ON m.user_id = u.id GROUP BY u.id ORDER BY u.username"
        )
        return [public(r) for r in rows]

    def usage(self, user_id: str) -> int:
        row = self.db.one("SELECT COALESCE(SUM(size + original_size), 0) AS b FROM media WHERE user_id=?",
                          (user_id,))
        return int(row["b"]) if row else 0

    def remaining_bytes(self, user: dict) -> int | None:
        """Freies Kontingent in Bytes (None = unbegrenzt)."""
        if not user.get("quota_mb"):
            return None
        return max(0, user["quota_mb"] * 1024 * 1024 - self.usage(user["id"]))

    def folder(self, user: dict | None) -> Path:
        """Ablageordner des Benutzers unterhalb von DOWNLOAD_PATH."""
        if not user:
            return self.s.download_path
        return self.s.download_path / user["username"]

    def admin_count(self) -> int:
        row = self.db.one("SELECT COUNT(*) AS n FROM users WHERE role='admin' AND disabled=0")
        return row["n"] if row else 0

    # ----------------------------------------------------------- Anmeldung
    def authenticate(self, username: str, password: str) -> dict | None:
        user = self.by_username(username)
        ok = verify_password(password, user["password_hash"] if user else self._dummy_hash)
        if not user or not ok or user["disabled"]:
            return None
        return user

    # ----------------------------------------------------------- Ändern
    def _validate_password(self, password: str) -> None:
        if not isinstance(password, str) or len(password) < MIN_PASSWORD:
            raise UserError(f"Das Passwort muss mindestens {MIN_PASSWORD} Zeichen lang sein.")
        if len(password) > 500:
            raise UserError("Das Passwort ist zu lang.")

    def create(self, username: str, password: str, role: str = "user", quota_mb=None, max_jobs=None,
               validate_password: bool = True) -> dict:
        username = (username or "").strip().lower()
        if not USERNAME_RE.match(username):
            raise UserError("Benutzername: 2–32 Zeichen, nur a–z, 0–9, Punkt, Binde- und Unterstrich.")
        if username.startswith(".") or username in ("feed", "admin-reserved"):
            raise UserError("Dieser Benutzername ist nicht erlaubt.")
        if role not in ROLES:
            raise UserError("Ungültige Rolle.")
        if validate_password:
            self._validate_password(password)
        if self.by_username(username):
            raise UserError("Dieser Benutzername ist bereits vergeben.", status_code=409)
        user = {
            "id": uuid.uuid4().hex,
            "username": username,
            "password_hash": hash_password(password),
            "role": role,
            "quota_mb": _limit(quota_mb),
            "max_jobs": _limit(max_jobs),
            "feed_token": secrets.token_urlsafe(24),
            "disabled": 0,
            "session_version": 1,
            "created_at": utcnow(),
        }
        self.db.insert("users", user)
        log.info("Benutzer angelegt: %s (%s)", username, role)
        return user

    def update(self, user_id: str, changes: dict, actor: dict) -> dict:
        user = self.require(user_id)
        data: dict = {}
        if "role" in changes and changes["role"] is not None:
            if changes["role"] not in ROLES:
                raise UserError("Ungültige Rolle.")
            data["role"] = changes["role"]
        if "disabled" in changes and changes["disabled"] is not None:
            data["disabled"] = 1 if changes["disabled"] else 0
        for key in ("quota_mb", "max_jobs"):
            if key in changes:
                data[key] = _limit(changes[key])
        demoted = data.get("role", user["role"]) != "admin" or data.get("disabled", user["disabled"])
        if user["role"] == "admin" and not user["disabled"] and demoted and self.admin_count() <= 1:
            raise UserError("Es muss mindestens ein aktiver Admin bleiben.", status_code=409)
        if user_id == actor["id"] and demoted:
            raise UserError("Du kannst dir die Admin-Rechte nicht selbst entziehen.", status_code=409)
        if changes.get("password"):
            self._validate_password(changes["password"])
            data["password_hash"] = hash_password(changes["password"])
        if "password_hash" in data or data.get("disabled") or data.get("role", user["role"]) != user["role"]:
            data["session_version"] = user["session_version"] + 1  # bestehende Sitzungen beenden
        self.db.update("users", user_id, data)
        return self.require(user_id)

    def change_password(self, user: dict, current: str, new: str) -> dict:
        if not verify_password(current, user["password_hash"]):
            raise UserError("Das aktuelle Passwort ist falsch.", status_code=403)
        self._validate_password(new)
        self.db.update("users", user["id"], {
            "password_hash": hash_password(new), "session_version": user["session_version"] + 1,
        })
        return self.require(user["id"])

    def rotate_feed_token(self, user_id: str) -> str:
        token = secrets.token_urlsafe(24)
        self.db.update("users", user_id, {"feed_token": token})
        return token

    def delete(self, user_id: str) -> None:
        self.db.execute("DELETE FROM users WHERE id=?", (user_id,))

    # ----------------------------------------------------------- Start
    def bootstrap(self, initial_password: str) -> dict:
        """Legt beim ersten Start den Admin an und übernimmt Bestandsdaten (Migration aus v1)."""
        admin = self.db.one("SELECT * FROM users WHERE role='admin' ORDER BY created_at LIMIT 1")
        if not admin:
            username = (self.s.admin_username or "admin").strip().lower()
            existing = self.by_username(username) if USERNAME_RE.match(username) else None
            if existing:
                self.db.update("users", existing["id"], {"role": "admin"})
                admin = self.require(existing["id"])
            else:
                if not USERNAME_RE.match(username):
                    log.warning("ADMIN_USERNAME '%s' ist ungültig – verwende 'admin'", username)
                    username = "admin"
                admin = self.create(username, initial_password, role="admin", validate_password=False)
        # v1-Bestand: Aufträge/Medien ohne Besitzer gehören dem Admin
        n_media = self.db.execute("UPDATE media SET user_id=? WHERE user_id IS NULL", (admin["id"],))
        n_jobs = self.db.execute("UPDATE jobs SET user_id=? WHERE user_id IS NULL", (admin["id"],))
        if n_media or n_jobs:
            log.info("Bestandsdaten übernommen: %d Medien, %d Aufträge -> %s", n_media, n_jobs, admin["username"])
        # bisheriger globaler Feed-Token bleibt für den Admin gültig (bestehende Abos)
        legacy = self.db.kv_get(LEGACY_FEED_KEY)
        if legacy:
            self.db.update("users", admin["id"], {"feed_token": legacy})
            self.db.execute("DELETE FROM kv WHERE key=?", (LEGACY_FEED_KEY,))
        return self.require(admin["id"])

    def reset_admin_password(self, password: str) -> None:
        user = self.by_username(self.s.admin_username)
        if user:
            self.db.update("users", user["id"], {
                "password_hash": hash_password(password), "disabled": 0, "role": "admin",
                "session_version": user["session_version"] + 1,
            })
            log.warning("Passwort von '%s' per ADMIN_PASSWORD_RESET zurückgesetzt", user["username"])
