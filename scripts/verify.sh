#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
repo_root=$(CDPATH= cd -P "$script_dir/.." && pwd)
cd "$repo_root"

run_api() {
  (
    cd apps/api
    uv sync --frozen --extra dev
    uv run --frozen --extra dev ruff check .
    uv run --frozen --extra dev ruff format --check .
    PYTHONDONTWRITEBYTECODE=1 uv run --frozen --extra dev \
      python -m pytest -p no:cacheprovider -q
  )
}

run_web() {
  (
    cd apps/web
    npm ci
    npm run lint
    npm run build
    npm test
    npm run check-nav
  )
}

run_docker() {
  command -v docker >/dev/null 2>&1 || {
    echo "docker is required for the docker verification target" >&2
    return 127
  }
  sh -n deploy/ops-agent.sh
  HOST_ENV_FILE=./host.env.example \
    docker compose -f deploy/docker-compose.yml config --quiet
  docker build -t robopark-api:verify apps/api
  docker build -t robopark-web:verify apps/web
  docker run --rm --entrypoint python robopark-api:verify -c \
    "import importlib.util, multipart, robopark_api; assert importlib.util.find_spec('pytest') is None"
}

usage() {
  echo "usage: $0 [api|web|docker]" >&2
}

if [ "$#" -gt 1 ]; then
  usage
  exit 2
fi

case "${1:-all}" in
  api)
    run_api
    ;;
  web)
    run_web
    ;;
  docker)
    run_docker
    ;;
  all)
    run_api
    run_web
    run_docker
    ;;
  *)
    usage
    exit 2
    ;;
esac
