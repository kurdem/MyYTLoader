"""Gemeinsame FastAPI-Abhängigkeiten."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from .auth import Auth
from .config import Settings
from .db import Database
from .feed import FeedTokens
from .jobs import EventBus, JobManager
from .library import Library
from .media import FFmpeg
from .security import RateLimiter
from .ytdlp import YtDlp


@dataclass
class Services:
    settings: Settings
    db: Database
    auth: Auth
    bus: EventBus
    ytdlp: YtDlp
    ffmpeg: FFmpeg
    library: Library
    jobs: JobManager
    feed: FeedTokens
    api_limiter: RateLimiter
    login_limiter: RateLimiter


def services(request: Request) -> Services:
    return request.app.state.services


def require_user(request: Request) -> str:
    return services(request).auth.require(request)
