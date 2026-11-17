from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from ..auth import COOKIE_NAME, client_ip, is_https
from ..deps import CurrentUser, services
from ..security import UserError
from ..users import public

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str = Field(max_length=200)
    password: str = Field(max_length=500)


class PasswordBody(BaseModel):
    current: str = Field(max_length=500)
    new: str = Field(max_length=500)


def _set_cookie(request: Request, response: Response, user: dict) -> None:
    svc = services(request)
    response.set_cookie(
        COOKIE_NAME, svc.auth.issue(user), max_age=svc.auth.max_age,
        httponly=True, samesite="lax", secure=is_https(request), path="/",
    )


def account(svc, user: dict) -> dict:
    remaining = svc.users.remaining_bytes(user)
    return {
        **public(user),
        "usage_bytes": svc.users.usage(user["id"]),
        "quota_bytes": user["quota_mb"] * 1024 * 1024 if user.get("quota_mb") else None,
        "remaining_bytes": remaining,
    }


@router.get("/me")
def me(request: Request) -> dict:
    svc = services(request)
    user = svc.auth.session_user(request)
    return {
        "auth_enabled": svc.auth.enabled,
        "authenticated": bool(user),
        "user": account(svc, user) if user else None,
    }


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response) -> dict:
    svc = services(request)
    if not svc.auth.enabled:
        return {"ok": True}
    svc.login_limiter.check(client_ip(request))
    user = svc.users.authenticate(body.username, body.password)
    if not user:
        raise UserError("Benutzername oder Passwort ist falsch.", status_code=401)
    _set_cookie(request, response, user)
    return {"ok": True}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.post("/password")
def change_password(body: PasswordBody, user: CurrentUser, request: Request, response: Response) -> dict:
    svc = services(request)
    svc.login_limiter.check(client_ip(request))
    updated = svc.users.change_password(user, body.current, body.new)
    _set_cookie(request, response, updated)  # andere Sitzungen werden ungültig, diese bleibt
    return {"ok": True}
