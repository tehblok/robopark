#!/bin/sh
set -e
cd /app
workers=${UVICORN_WORKERS:-2}
case "$workers" in
  ''|*[!0-9]*) echo 'UVICORN_WORKERS must be an integer from 1 to 4' >&2; exit 1 ;;
esac
if [ "$workers" -gt 4 ]; then
  workers=4
fi
if [ "$workers" -lt 1 ]; then
  workers=1
fi
alembic upgrade head
exec uvicorn robopark_api.main:app --host 0.0.0.0 --port 8000 --workers "$workers" \
  --backlog 512 --timeout-graceful-shutdown 30
