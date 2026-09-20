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
  if [[ -z "${ROBOPARK_SOAK_DURATION_SECONDS:-}" || -z "${ROBOPARK_SOAK_OUTPUT:-}" ]]; then
    echo "ROBOPARK_SOAK_DURATION_SECONDS and ROBOPARK_SOAK_OUTPUT are required for soak" >&2
    exit 2
  fi
  if [[ ! "${ROBOPARK_SOAK_DURATION_SECONDS}" =~ ^[0-9]+([.][0-9]+)?$ ]] || [[ "${ROBOPARK_SOAK_DURATION_SECONDS}" == "0" ]]; then
    echo "ROBOPARK_SOAK_DURATION_SECONDS must be a positive number" >&2
    exit 2
  fi
  soak_output="${ROBOPARK_SOAK_OUTPUT}"
  if [[ "$soak_output" == /* || "$soak_output" == ".." || "$soak_output" == ../* || "$soak_output" == */../* ]]; then
    echo "ROBOPARK_SOAK_OUTPUT must be a relative path inside apps/web" >&2
    exit 2
  fi
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
trap 'rm -rf "$visual_workspace"' EXIT

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

docker run --rm --ipc=host \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp/robopark-playwright-home \
  -e UV_CACHE_DIR=/tmp/robopark-uv-cache \
  -e ROBOPARK_PLAYWRIGHT_MODE="$mode" \
  -e ROBOPARK_SOAK_DURATION_SECONDS="${ROBOPARK_SOAK_DURATION_SECONDS:-}" \
  -e ROBOPARK_SOAK_OUTPUT="${soak_output:+/work/apps/web/$soak_output}" \
  -v "$visual_workspace:/work" \
  -w /work/apps/api \
  "$image" \
  bash -lc '
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
  ' robopark-playwright "${playwright_args[@]}"

if [[ "$mode" == "soak" ]]; then
  mkdir -p "$(dirname "$soak_output")"
  cp "$visual_workspace/apps/web/$soak_output" "$soak_output"
fi

if [[ "$mode" == "update" ]]; then
  rsync -a --prune-empty-dirs \
    --include '*/' --include '*-snapshots/***' --exclude '*' \
    "$visual_workspace/apps/web/e2e/" ./e2e/
fi
