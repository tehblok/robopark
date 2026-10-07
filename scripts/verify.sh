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
      python -m pytest -p no:cacheprovider -q -m "not load"
  )
}

# Deliberately bounded developer/PR gate.  It does not start containers,
# build images, install browsers, or run capacity/soak scenarios.
run_fast() {
  (
    cd apps/api
    uv sync --frozen --extra dev
    uv run --frozen --extra dev ruff check src/robopark_api tests/test_lifespan_jobs.py
    PYTHONDONTWRITEBYTECODE=1 uv run --frozen --extra dev \
      python -m pytest -p no:cacheprovider -q \
      tests/test_lifespan_jobs.py \
      tests/test_login_throttle.py \
      tests/test_tracker_notifications.py \
      tests/test_system_notifications.py \
      tests/test_push.py
  )
  (
    cd apps/web
    npm run lint
    npm run check-nav
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
    npm run test:scripts
    npm run check-nav
  )
}

run_bot() {
  PYTHONDONTWRITEBYTECODE=1 \
    uv run --project apps/api --frozen --extra dev \
      --with-requirements apps/bot/requirements.lock \
      python -m pytest -p no:cacheprovider -q \
      tests/bot/test_native_reports.py \
      tests/bot/test_native_campaigns.py \
      tests/bot/test_native_qr.py \
      tests/bot/test_native_service.py \
      tests/bot/test_native_transport.py \
      tests/bot/test_bot_runtime_packaging.py
}

run_docker() {
  command -v docker >/dev/null 2>&1 || {
    echo "docker is required for the docker verification target" >&2
    return 127
  }
  sh -n deploy/ops-agent.sh
  verify_placeholder=$repo_root/deploy/host.env.example
  HOST_ENV_FILE=./host.env.example \
    ROBOPARK_POSTGRES_PASSWORD_FILE="$verify_placeholder" \
    ROBOPARK_PGPASS_FILE="$verify_placeholder" \
    ROBOPARK_SNAPSHOT_CONFIG_FILE="$verify_placeholder" \
    docker compose --project-name robopark -f deploy/docker-compose.yml config --quiet
  docker build -t robopark-api:verify apps/api
  docker build -t robopark-web:verify apps/web
  docker run --rm --entrypoint python robopark-api:verify -c \
    "import importlib.util, multipart, robopark_api; assert importlib.util.find_spec('pytest') is None"
}

run_host() {
  PYTHONDONTWRITEBYTECODE=1 \
    uv run --project "$repo_root/apps/api" --frozen --extra dev \
      python scripts/check-release-migrations.py
  PYTHONDONTWRITEBYTECODE=1 \
    uv run --project "$repo_root/apps/api" --frozen --extra dev \
      python scripts/generate-release-notes.py --check
  for script in deploy/installer/install.sh deploy/installer/lib/*.sh deploy/tuna-http.sh; do
    sh -n "$script"
  done
  PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$repo_root/deploy/ota:$repo_root/deploy/host:$repo_root/apps/api/src" \
    uv run --project "$repo_root/apps/api" --frozen --extra dev \
      python -m pytest -p no:cacheprovider tests/host -q
}

run_ota() {
  PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$repo_root/deploy/ota:$repo_root/deploy/host" \
    uv run --project "$repo_root/apps/api" --frozen --extra dev \
      python -m pytest -p no:cacheprovider -q \
      tests/host/test_ota_verifier.py \
      tests/host/test_ota_builder.py \
      tests/host/test_ota_menu.py \
      tests/host/test_ota_clean_install.py \
      tests/host/test_ota_credentials.py \
      tests/host/test_ota_store.py \
      tests/host/test_ota_update.py \
      tests/host/test_ota_acceptance.py \
      apps/api/tests/test_admin_ota.py \
      apps/api/tests/test_seed_from_file.py
}

run_load() {
  command -v docker >/dev/null 2>&1 || {
    echo "docker is required for the load verification target" >&2
    return 127
  }
  PYTHONDONTWRITEBYTECODE=1 \
    uv run --project apps/api --frozen --extra dev \
      python scripts/capacity_benchmark.py --users "${ROBOPARK_LOAD_USERS:-200}" \
      --workers "${ROBOPARK_LOAD_WORKERS:-2}" \
      --duration "${ROBOPARK_LOAD_DURATION_SECONDS:-60}"
}

run_soak() {
  : "${ROBOPARK_SOAK_DURATION_SECONDS:?set an explicit soak duration in seconds}"
  : "${ROBOPARK_SOAK_OUTPUT:?set an explicit soak output path}"
  soak_token=${ROBOPARK_SOAK_RUN_TOKEN:-$(node -e 'process.stdout.write(require("node:crypto").randomUUID())')}
  ROBOPARK_E2E_SUITE=soak ROBOPARK_SOAK_DURATION_SECONDS="$ROBOPARK_SOAK_DURATION_SECONDS" \
    ROBOPARK_SOAK_RUN_TOKEN="$soak_token" \
    ROBOPARK_SOAK_OUTPUT="$ROBOPARK_SOAK_OUTPUT" \
    npm --prefix apps/web run test:e2e:soak
}

usage() {
  printf '%s\n' \
    "usage: $0 [fast|full|load|soak|api|api-postgres|web|bot|docker|host|ota|terminal-linux]" \
    "" \
    "fast  Short static and focused regression checks (typically under 2 minutes)." \
    "full  Full API, PostgreSQL, web, Docker and host verification; may take many minutes." \
    "load  Explicit capacity benchmark; starts disposable PostgreSQL and production workers." \
    "soak  Explicit browser soak; requires ROBOPARK_SOAK_DURATION_SECONDS and ROBOPARK_SOAK_OUTPUT." >&2
}

if [ "$#" -gt 1 ]; then
  usage
  exit 2
fi

case "${1:-all}" in
  terminal-linux)
    # Explicit opt-in only. The Python guard refuses unmarked/non-systemd hosts.
    : "${ROBOPARK_TERMINAL_ACCEPTANCE_OUTPUT:?Set an absolute report path inside the disposable VM}"
    python3 scripts/terminal_acceptance.py --output "$ROBOPARK_TERMINAL_ACCEPTANCE_OUTPUT"
    ;;
  fast)
    run_fast
    ;;
  load)
    run_load
    ;;
  soak)
    run_soak
    ;;
  api)
    run_api
    ;;
  api-postgres)
    run_api_postgres
    ;;
  web)
    run_web
    ;;
  bot)
    run_bot
    ;;
  docker)
    run_docker
    ;;
  host)
    run_host
    ;;
  ota)
    run_ota
    ;;
  full|all)
    run_api
    run_api_postgres
    run_bot
    run_web
    run_docker
    run_host
    ;;
  *)
    usage
    exit 2
    ;;
esac
