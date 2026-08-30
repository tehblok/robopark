FROM python:3.12-slim

# curl is needed by the container healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

COPY pyproject.toml ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic
COPY data/emergency_sections.json ./data/emergency_sections.json

RUN pip install --no-cache-dir . pytest

COPY docker-entrypoint.sh ./docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# Run as an unprivileged user; /data is the mounted SQLite volume.
# UVICORN_WORKERS default 2, cap 4 (see docker-entrypoint.sh). Do not raise
# above 1 until live-merge overlap tests pass in CI.
RUN useradd --create-home --uid 10001 robopark \
    && mkdir -p /data \
    && chown -R robopark:robopark /app /data
USER robopark

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["/app/docker-entrypoint.sh"]
