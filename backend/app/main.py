"""FastAPI-Anwendung: Services verdrahten, Routen, statisches Frontend, Sicherheitsheader."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .auth import Auth
from .branding import Branding
from .config import Settings, get_settings
from .db import Database
from .deps import Services
from .jobs import EventBus, JobManager
from .library import Library
from .maintenance import maintenance_loop
from .media import FFmpeg
from .routes import auth as auth_routes
from .routes import branding as branding_routes
from .routes import feed as feed_routes
from .routes import jobs as job_routes
from .routes import library as library_routes
from .routes import system as system_routes
from .routes import users as user_routes
from .security import RateLimiter, UserError
from .users import Users
from .ytdlp import YtDlp

log = logging.getLogger("ytloader")

CSP = (
    "default-src 'self'; "
    "img-src 'self' data: blob: https://*.ytimg.com https://*.ggpht.com https://*.googleusercontent.com; "
    "media-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; "
    "manifest-src 'self'; worker-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


def build_services(settings: Settings) -> Services:
    for path in (settings.download_path, settings.temp_path, settings.database_path, settings.config_path):
        path.mkdir(parents=True, exist_ok=True)
    db = Database(settings.database_path / "ytloader.db")
    bus = EventBus()
    ffmpeg = FFmpeg(settings.ffmpeg_bin, settings.ffprobe_bin)
    ytdlp = YtDlp(settings)
    users = Users(settings, db)
    auth = Auth(settings, users)  # legt beim ersten Start den Admin an / übernimmt Bestandsdaten
    library = Library(settings, db, users, bus)
    jobs = JobManager(settings, db, ytdlp, ffmpeg, library, users, bus)
    return Services(
        settings=settings, db=db, users=users, auth=auth, bus=bus, ytdlp=ytdlp, ffmpeg=ffmpeg,
        library=library, jobs=jobs,
        api_limiter=RateLimiter(settings.rate_limit_per_minute, 60),
        login_limiter=RateLimiter(10, 300),
        branding=Branding(settings, db, ffmpeg),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = build_services(settings)
        app.state.services = svc
        svc.bus.bind(asyncio.get_running_loop())
        await svc.jobs.start()
        maint = asyncio.create_task(maintenance_loop(svc), name="maintenance")
        if not settings.auth_enabled:
            log.warning("AUTH_ENABLED=false – die Oberfläche ist ohne Anmeldung erreichbar!")
        log.info("MyYTLoader %s gestartet als '%s' (yt-dlp %s)", __version__, svc.branding.name, svc.ytdlp.version())
        try:
            yield
        finally:
            maint.cancel()
            await svc.jobs.stop()
            svc.db.close()

    app = FastAPI(title="MyYTLoader", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(UserError)
    async def user_error(_: Request, exc: UserError):
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        fields = ", ".join(str(e.get("loc", ["?"])[-1]) for e in exc.errors())
        return JSONResponse({"detail": f"Ungültige Eingabe ({fields})."}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else "Fehler"
        if exc.status_code == 404:
            detail = "Nicht gefunden."
        return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=exc.headers)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Content-Security-Policy", CSP)
        path = request.url.path
        if path.startswith("/api/") and "Cache-Control" not in h:
            h["Cache-Control"] = "no-store"
        elif path in ("/", "/index.html", "/sw.js", "/manifest.webmanifest"):
            h["Cache-Control"] = "no-cache"
        return response

    app.include_router(branding_routes.public)
    app.include_router(branding_routes.api)
    app.include_router(system_routes.public)
    app.include_router(system_routes.router)
    app.include_router(auth_routes.router)
    app.include_router(user_routes.router)
    app.include_router(job_routes.router)
    app.include_router(library_routes.router)
    app.include_router(feed_routes.private)
    app.include_router(feed_routes.public)

    if settings.frontend_path.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_path, html=True), name="frontend")
    else:
        log.warning("Frontend-Verzeichnis %s fehlt", settings.frontend_path)
    return app

