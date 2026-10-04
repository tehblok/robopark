FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

ARG UV_VERSION=0.11.31
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends postgresql-client-17 \
    && rm -rf /var/lib/apt/lists/*
ARG PIP_VERSION=26.2.1
RUN pip install --no-cache-dir --upgrade "pip==${PIP_VERSION}"

RUN pip install --no-cache-dir "uv==${UV_VERSION}"

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH=/app/.venv/bin:$PATH

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic
COPY data/emergency_sections.json ./data/emergency_sections.json
RUN uv sync --frozen --no-dev

# Dependency installation is finished; the runtime does not need pip or its
# vendored dependency tree. Keep the application environment locked by uv.
RUN /usr/local/bin/python -m pip uninstall --yes pip

COPY docker-entrypoint.sh ./docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# Run as an unprivileged user; /data contains attachments and ops artifacts.
RUN useradd --create-home --uid 10001 robopark \
    && mkdir -p /data \
    && chown -R robopark:robopark /app /data
# Shipped knowledge is immutable; user changes and indexing belong in the DB.
COPY --chown=root:root knowledge ./knowledge
RUN find /app/knowledge -type d -exec chmod 0555 {} + \
    && find /app/knowledge -type f -exec chmod 0444 {} +
USER robopark

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=4)"]

CMD ["/app/docker-entrypoint.sh"]
