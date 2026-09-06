#!/usr/bin/env bash
set -euo pipefail

mode="${1:-test}"
shift || true
if [[ "$mode" != "test" && "$mode" != "update" ]]; then
  echo "usage: $0 <test|update> [playwright arguments...]" >&2
  exit 2
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
  -v "$visual_workspace:/work" \
  -w /work/apps/api \
  "$image" \
  bash -lc 'uv sync --frozen --extra dev && cd ../web && npm ci && exec npx playwright test "$@"' robopark-playwright "${playwright_args[@]}"

if [[ "$mode" == "update" ]]; then
  rsync -a --prune-empty-dirs \
    --include '*/' --include '*-snapshots/***' --exclude '*' \
    "$visual_workspace/apps/web/e2e/" ./e2e/
fi
