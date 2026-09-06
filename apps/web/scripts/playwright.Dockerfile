ARG PLAYWRIGHT_VERSION=1.62.1
FROM ghcr.io/astral-sh/uv:0.11.31 AS uv
FROM mcr.microsoft.com/playwright:v${PLAYWRIGHT_VERSION}-noble
COPY --from=uv /uv /uvx /usr/local/bin/
# Same Python patch version as the verified native API environment.
ENV UV_PYTHON_INSTALL_DIR=/opt/robopark-python
RUN uv python install 3.13.14
ENV UV_PYTHON=3.13.14
