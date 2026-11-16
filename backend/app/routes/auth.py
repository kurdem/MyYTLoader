from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from ..auth import COOKIE_NAME, client_ip, is_https
from ..deps import services
from ..security import UserError

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str = Field(max_length=200)
    password: str = Field(max_length=500)


@router.get("/me")
def me(request: Request) -> dict:
    svc = services(request)
    user = svc.auth.session_user(request)
    return {"auth_enabled": svc.auth.enabled, "authenticated": bool(user), "username": user}


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response) -> dict:
    svc = services(request)
    if not svc.auth.enabled:
        return {"ok": True}
    svc.login_limiter.check(client_ip(request))
    if not svc.auth.check_credentials(body.username.strip(), body.password):
        raise UserError("Benutzername oder Passwort ist falsch.", status_code=401)
    response.set_cookie(
        COOKIE_NAME, svc.auth.issue(svc.auth.username), max_age=svc.auth.max_age,
        httponly=True, samesite="lax", secure=is_https(request), path="/",
    )
    return {"ok": True}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}
