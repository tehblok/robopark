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

run_api_postgres() {
  command -v docker >/dev/null 2>&1 || {
    echo "docker is required for the PostgreSQL verification target" >&2
    return 127
  }
  (
    cd apps/api
    uv sync --frozen --extra dev
    ROBOPARK_POSTGRES_TESTS=1 PYTHONDONTWRITEBYTECODE=1 \
      uv run --frozen --extra dev python -m pytest -p no:cacheprovider -q tests/postgres
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
    docker compose --project-name robopark -f deploy/docker-compose.yml config --quiet
  docker build -t robopark-api:verify apps/api
  docker build -t robopark-web:verify apps/web
  docker run --rm --entrypoint python robopark-api:verify -c \
    "import importlib.util, multipart, robopark_api; assert importlib.util.find_spec('pytest') is None"
}

run_host() {
  for script in deploy/installer/install.sh deploy/installer/lib/*.sh deploy/tuna-http.sh; do
    sh -n "$script"
  done
  PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$repo_root/deploy/host:$repo_root/apps/api/src" \
    uv run --project "$repo_root/apps/api" --frozen --extra dev \
      python -m pytest -p no:cacheprovider tests/host -q
  PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$repo_root/deploy/host:$repo_root/apps/api/src" \
    uv run --project "$repo_root/apps/api" --frozen --extra dev \
      python tests/host/installer_scenarios.py
}

usage() {
  echo "usage: $0 [api|api-postgres|web|docker|host|all]" >&2
}

if [ "$#" -gt 1 ]; then
  usage
  exit 2
fi

case "${1:-all}" in
  api)
    run_api
    ;;
  api-postgres)
    run_api_postgres
    ;;
  web)
    run_web
    ;;
  docker)
    run_docker
    ;;
  host)
    run_host
    ;;
  all)
    run_api
    run_web
    run_docker
    run_host
    ;;
  *)
    usage
    exit 2
    ;;
esac
