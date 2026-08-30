#!/bin/sh
set -e
cd /app
alembic upgrade head
workers=${UVICORN_WORKERS:-2}
if [ "$workers" -gt 4 ]; then
  workers=4
fi
if [ "$workers" -lt 1 ]; then
  workers=1
fi
exec uvicorn robopark_api.main:app --host 0.0.0.0 --port 8000 --workers "$workers"
