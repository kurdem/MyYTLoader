#!/bin/sh
# Startet MyYTLoader. Läuft der Container als root, werden die Volumes an PUID/PGID
# angepasst und die Rechte anschließend abgegeben.
set -eu

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

if [ "$(id -u)" = "0" ]; then
  for dir in "$DOWNLOAD_PATH" "$TEMP_PATH" "$DATABASE_PATH" "$CONFIG_PATH"; do
    mkdir -p "$dir"
    if [ "$(stat -c '%u:%g' "$dir")" != "$PUID:$PGID" ]; then
      chown "$PUID:$PGID" "$dir"
    fi
  done
  # kleine Verzeichnisse vollständig (z. B. nach PUID-Wechsel)
  chown -R "$PUID:$PGID" "$TEMP_PATH" "$DATABASE_PATH" "$CONFIG_PATH"
  RUN_AS="setpriv --reuid=$PUID --regid=$PGID --clear-groups --"
else
  RUN_AS=""
fi

cd /app/backend
# shellcheck disable=SC2086
exec $RUN_AS python -m uvicorn app.main:create_app --factory \
  --host 0.0.0.0 \
  --port "${APP_PORT:-8080}" \
  --workers 1 \
  --proxy-headers \
  --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-*}" \
  --no-server-header \
  --timeout-graceful-shutdown 10 \
  --log-level "$(echo "${LOG_LEVEL:-info}" | tr '[:upper:]' '[:lower:]')"
