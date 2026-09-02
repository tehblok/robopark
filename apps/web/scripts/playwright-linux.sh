#!/usr/bin/env bash
set -euo pipefail

mode="${1:-test}"
shift || true
if [[ "$mode" != "test" && "$mode" != "update" ]]; then
  echo "usage: $0 <test|update> [playwright arguments...]" >&2
  exit 2
fi

playwright_version="$(node -p "require('./node_modules/@playwright/test/package.json').version")"
visual_workspace="$(mktemp -d "${TMPDIR:-/tmp}/robopark-playwright.XXXXXX")"
trap 'rm -rf "$visual_workspace"' EXIT

rsync -a \
  --exclude node_modules \
  --exclude dist \
  --exclude playwright-report \
  --exclude test-results \
  ./ "$visual_workspace/"

playwright_args=("$@")
if [[ "$mode" == "update" ]]; then
  playwright_args=(--update-snapshots "${playwright_args[@]}")
fi

docker run --rm --ipc=host \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp/robopark-playwright-home \
  -v "$visual_workspace:/work" \
  -w /work \
  "mcr.microsoft.com/playwright:v${playwright_version}-noble" \
  bash -lc 'npm ci && exec npx playwright test "$@"' robopark-playwright "${playwright_args[@]}"

if [[ "$mode" == "update" ]]; then
  rsync -a --prune-empty-dirs \
    --include '*/' --include '*-snapshots/***' --exclude '*' \
    "$visual_workspace/e2e/" ./e2e/
fi
