"""Gemeinsame FastAPI-Abhängigkeiten."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from .auth import Auth, is_admin
from .branding import Branding
from .customfeeds import CustomFeeds
from .config import Settings
from .db import Database
from .jobs import EventBus, JobManager
from .library import Library
from .media import FFmpeg
from .security import RateLimiter, UserError
from .subscriptions import Subscriptions
from .users import Users
from .ytdlp import YtDlp


@dataclass
class Services:
    settings: Settings
    db: Database
    users: Users
    auth: Auth
    bus: EventBus
    ytdlp: YtDlp
    ffmpeg: FFmpeg
    library: Library
    jobs: JobManager
    api_limiter: RateLimiter
    login_limiter: RateLimiter
    branding: Branding
    feeds: CustomFeeds
    subscriptions: Subscriptions


def services(request: Request) -> Services:
    return request.app.state.services


def require_user(request: Request) -> dict:
    return services(request).auth.require(request)


def require_admin(request: Request) -> dict:
    user = require_user(request)
    if not is_admin(user):
        raise UserError("Nur für Administratoren.", status_code=403)
    return user


CurrentUser = Annotated[dict, Depends(require_user)]
AdminUser = Annotated[dict, Depends(require_admin)]
