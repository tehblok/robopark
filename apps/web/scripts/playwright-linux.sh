#!/usr/bin/env bash
set -euo pipefail

mode="${1:-test}"
shift || true
if [[ "$mode" != "test" && "$mode" != "update" && "$mode" != "pwa" && "$mode" != "soak" ]]; then
  echo "usage: $0 <test|update|pwa|soak> [playwright arguments...]" >&2
  exit 2
fi

soak_output=""
if [[ "$mode" == "soak" ]]; then
  if [[ -z "${ROBOPARK_SOAK_DURATION_SECONDS:-}" || -z "${ROBOPARK_SOAK_OUTPUT:-}" || -z "${ROBOPARK_SOAK_RUN_TOKEN:-}" ]]; then
    echo "ROBOPARK_SOAK_DURATION_SECONDS, ROBOPARK_SOAK_OUTPUT and ROBOPARK_SOAK_RUN_TOKEN are required for soak" >&2
    exit 2
  fi
  if ! node -e 'const value = Number(process.argv[1]); process.exit(Number.isFinite(value) && value > 0 ? 0 : 1)' \
    "${ROBOPARK_SOAK_DURATION_SECONDS}"; then
    echo "ROBOPARK_SOAK_DURATION_SECONDS must be a positive finite number" >&2
    exit 2
  fi
  soak_output="${ROBOPARK_SOAK_OUTPUT}"
  if [[ -e "$soak_output" ]]; then
    echo "ROBOPARK_SOAK_OUTPUT already exists: $soak_output" >&2
    exit 2
  fi
fi

command -v docker >/dev/null 2>&1 || {
  echo "docker is required for Linux browser verification" >&2
  exit 127
}

playwright_version="$(node -p "require('./node_modules/@playwright/test/package.json').version")"
visual_workspace="$(mktemp -d "${TMPDIR:-/tmp}/robopark-playwright.XXXXXX")"
container_name="robopark-playwright-$(id -u)-$$"
artifact_output="${ROBOPARK_PLAYWRIGHT_ARTIFACTS:-test-results/linux-$(date +%Y%m%d-%H%M%S)-$$}"
cleanup() {
  # Preserve evidence on failures too, before removing the isolated container.
  mkdir -p "$artifact_output"
  docker cp "$container_name:/work/apps/web/test-results/." "$artifact_output/" >/dev/null 2>&1 || true
  if [[ "$mode" == "soak" && ! -e "$soak_output" ]]; then
    mkdir -p "$(dirname "$soak_output")"
    docker cp "$container_name:/tmp/robopark-soak-report.json" "$soak_output" >/dev/null 2>&1 || true
  fi
  docker rm -f "$container_name" >/dev/null 2>&1 || true
  rm -rf "$visual_workspace"
}
trap cleanup EXIT

mkdir -p "$visual_workspace/apps/web" "$visual_workspace/apps/api"

rsync -a \
  --exclude node_modules \
  --exclude dist \
  --exclude playwright-report \
  --exclude test-results \
  ./ "$visual_workspace/apps/web/"
rsync -a \
  --exclude .venv --exclude __pycache__ --exclude .pytest_cache \
  --exclude '.env' --exclude '.env.*' --exclude '*.env' \
  --exclude data --exclude logs --exclude '*.db' --exclude '*.sqlite' \
  ../api/ "$visual_workspace/apps/api/"
# The schema's public Emergency field definitions are needed by real API routes.
mkdir -p "$visual_workspace/apps/api/data"
cp ../api/data/emergency_sections.json "$visual_workspace/apps/api/data/"

image="robopark-playwright:${playwright_version}-py3.13.14-uv0.11.31"
docker build --build-arg "PLAYWRIGHT_VERSION=$playwright_version" \
  -t "$image" -f scripts/playwright.Dockerfile scripts

playwright_args=("$@")
if [[ "$mode" == "update" ]]; then
  playwright_args=(--update-snapshots=all "${playwright_args[@]}")
fi

# macOS still ships Bash 3.2, where expanding an empty array under `set -u`
# aborts before Docker receives the no-extra-arguments form.
set +u
docker create --name "$container_name" --ipc=host \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp/robopark-playwright-home \
  -e UV_CACHE_DIR=/tmp/robopark-uv-cache \
  -e ROBOPARK_PLAYWRIGHT_MODE="$mode" \
  -e ROBOPARK_SOAK_DURATION_SECONDS="${ROBOPARK_SOAK_DURATION_SECONDS:-}" \
  -e ROBOPARK_SOAK_OUTPUT="${soak_output:+/tmp/robopark-soak-report.json}" \
  -e ROBOPARK_SOAK_RUN_TOKEN="${ROBOPARK_SOAK_RUN_TOKEN:-}" \
  -w /work/apps/api \
  "$image" \
  bash -lc '
    set -euo pipefail
    uv sync --frozen --extra dev
    cd ../web
    npm ci
    case "$ROBOPARK_PLAYWRIGHT_MODE" in
      pwa)
        npm run build
        exec npx playwright test --config=playwright.pwa.config.ts "$@"
        ;;
      soak)
        export ROBOPARK_E2E_SUITE=soak
        mkdir -p "$(dirname "$ROBOPARK_SOAK_OUTPUT")"
        exec npx playwright test "$@"
        ;;
      *)
        exec npx playwright test "$@"
        ;;
    esac
  ' robopark-playwright "${playwright_args[@]}" >/dev/null
set -u

# Docker Desktop and Colima only share configured host roots. Copying the
# prepared workspace also works when TMPDIR lives under macOS /var/folders.
docker cp -a "$visual_workspace/." "$container_name:/work"
docker start -a "$container_name"

if [[ "$mode" == "soak" ]]; then
  mkdir -p "$(dirname "$soak_output")"
  docker cp "$container_name:/tmp/robopark-soak-report.json" "$soak_output"
fi

if [[ "$mode" == "update" ]]; then
  snapshot_workspace="$visual_workspace/updated-e2e"
  mkdir -p "$snapshot_workspace"
  docker cp "$container_name:/work/apps/web/e2e/." "$snapshot_workspace/"
  rsync -a --prune-empty-dirs \
    --include '*/' --include '*-snapshots/***' --exclude '*' \
    "$snapshot_workspace/" ./e2e/
fi
