FROM python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217

ARG UV_VERSION=0.11.31
RUN pip install --no-cache-dir "uv==${UV_VERSION}"

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    PATH=/app/.venv/bin:$PATH

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic
COPY data/emergency_sections.json ./data/emergency_sections.json
COPY certificates/YandexInternalRootCA.crt /usr/local/share/ca-certificates/
RUN update-ca-certificates
RUN uv sync --frozen --no-dev

COPY docker-entrypoint.sh ./docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# Run as an unprivileged user; /data is the mounted SQLite volume.
# UVICORN_WORKERS default 2, cap 4. Shared merge and file-SQLite concurrency
# are covered by API tests; measure on the target host before raising workers.
RUN useradd --create-home --uid 10001 robopark \
    && mkdir -p /data \
    && chown -R robopark:robopark /app /data
USER robopark

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=4)"]

CMD ["/app/docker-entrypoint.sh"]
