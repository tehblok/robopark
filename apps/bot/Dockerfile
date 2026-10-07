FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

WORKDIR /opt/robopark-bot

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/robopark-matplotlib

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock ./requirements.lock
ARG PIP_VERSION=26.2.1
RUN pip install --no-cache-dir --upgrade "pip==${PIP_VERSION}"

RUN pip install --no-cache-dir --require-hashes --requirement requirements.lock
RUN python -m pip uninstall --yes pip

COPY entrypoint.py ./entrypoint.py
COPY native ./native

RUN useradd --create-home --uid 10001 robopark \
    && mkdir -p /data \
    && chown -R robopark:robopark /opt/robopark-bot /data

USER robopark

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import os,time; from pathlib import Path; os.kill(1, 0); assert -5 <= time.time() - Path('/tmp/bot-ready').stat().st_mtime < 90"]

CMD ["python", "/opt/robopark-bot/entrypoint.py"]
