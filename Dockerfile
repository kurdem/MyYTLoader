# syntax=docker/dockerfile:1
# MyYTLoader – selbst gehosteter Media-Downloader & privater Podcast-Server
FROM python:3.13-slim-trixie

# yt-dlp-Basisversion im Image. Zur Laufzeit wird (abschaltbar) automatisch aktualisiert.
ARG YTDLP_VERSION=2026.10.26

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_PORT=8080 \
    DOWNLOAD_PATH=/downloads \
    TEMP_PATH=/temp \
    DATABASE_PATH=/database \
    CONFIG_PATH=/config \
    FRONTEND_PATH=/app/frontend \
    YTDLP_BUILTIN_VENV=/opt/yt-dlp \
    TMPDIR=/temp \
    HOME=/config \
    XDG_CACHE_HOME=/config/cache \
    DENO_DIR=/config/cache/deno \
    PUID=1000 \
    PGID=1000

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg tini ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# yt-dlp + Deno (JS-Runtime für YouTube) in eigenem venv – unabhängig von der App aktualisierbar
RUN python -m venv /opt/yt-dlp \
 && /opt/yt-dlp/bin/pip install "yt-dlp[default,deno]==${YTDLP_VERSION}" \
 && /opt/yt-dlp/bin/yt-dlp --version \
 && /opt/yt-dlp/bin/deno --version

WORKDIR /app/backend
COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY backend/app ./app
COPY frontend /app/frontend
COPY docker/entrypoint.sh /app/docker/entrypoint.sh
RUN chmod 0755 /app/docker/entrypoint.sh \
 && mkdir -p /downloads /temp /database /config

VOLUME ["/downloads", "/database", "/config", "/temp"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('APP_PORT','8080'), timeout=4)" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker/entrypoint.sh"]
