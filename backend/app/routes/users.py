"""Benutzerverwaltung (nur Admins)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..deps import AdminUser, services
from ..security import UserError
from ..users import public

router = APIRouter(prefix="/api/users", tags=["users"])


class CreateUser(BaseModel):
    username: str = Field(max_length=32)
    password: str = Field(max_length=500)
    role: Literal["admin", "user"] = "user"
    quota_mb: int | None = Field(default=None, ge=0)
    max_jobs: int | None = Field(default=None, ge=0)


class UpdateUser(BaseModel):
    role: Literal["admin", "user"] | None = None
    quota_mb: int | None = Field(default=None, ge=0)
    max_jobs: int | None = Field(default=None, ge=0)
    disabled: bool | None = None
    password: str | None = Field(default=None, max_length=500)


@router.get("")
def list_users(_: AdminUser, request: Request) -> dict:
    svc = services(request)
    s = svc.settings
    return {
        "users": svc.users.list(),
        "defaults": {"quota_mb": s.default_user_quota_mb or None, "max_jobs": s.default_user_max_jobs or None},
    }


@router.post("")
def create_user(body: CreateUser, _: AdminUser, request: Request) -> dict:
    svc = services(request)
    s = svc.settings
    quota = body.quota_mb if "quota_mb" in body.model_fields_set else s.default_user_quota_mb
    jobs = body.max_jobs if "max_jobs" in body.model_fields_set else s.default_user_max_jobs
    user = svc.users.create(body.username, body.password, body.role, quota, jobs)
    return public(user)


@router.patch("/{user_id}")
def update_user(user_id: str, body: UpdateUser, admin: AdminUser, request: Request) -> dict:
    svc = services(request)
    user = svc.users.update(user_id, body.model_dump(exclude_unset=True), actor=admin)
    if user["disabled"]:
        svc.jobs.cancel_all_of(user_id)
    return public(user)


@router.delete("/{user_id}")
def delete_user(user_id: str, admin: AdminUser, request: Request) -> dict:
    """Löscht das Konto inkl. aller Medien und Aufträge."""
    svc = services(request)
    user = svc.users.require(user_id)
    if user_id == admin["id"]:
        raise UserError("Du kannst dein eigenes Konto nicht löschen.", status_code=409)
    if user["role"] == "admin" and not user["disabled"] and svc.users.admin_count() <= 1:
        raise UserError("Es muss mindestens ein aktiver Admin bleiben.", status_code=409)
    # zuerst sperren, damit keine neuen Aufträge entstehen und Sitzungen enden
    svc.db.update("users", user_id, {"disabled": 1, "session_version": user["session_version"] + 1})
    svc.jobs.cancel_all_of(user_id)
    if svc.db.one("SELECT 1 FROM jobs WHERE user_id=? AND status IN ('running','processing')", (user_id,)):
        raise UserError("Laufende Aufträge werden gerade beendet. Bitte in ein paar Sekunden erneut versuchen.", 409)
    svc.subscriptions.delete_all_of(user_id)
    svc.feeds.delete_all_of(user_id)
    deleted = svc.library.delete_all_of(user_id)
    svc.db.execute("DELETE FROM jobs WHERE user_id=?", (user_id,))
    svc.users.delete(user_id)
    return {"ok": True, "deleted_media": deleted}
