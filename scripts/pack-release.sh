#!/usr/bin/env bash
# Pack a Robopark *release* ZIP (application tree), not a data snapshot.
# Usage: scripts/pack-release.sh [output.zip]
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
out="${1:-"$root/robopark-release.zip"}"
stage="$(mktemp -d "${TMPDIR:-/tmp}/robopark-release.XXXX")"
cleanup() { rm -rf "$stage"; }
trap cleanup EXIT

copy_tree() {
  local src="$1" dest="$2"
  mkdir -p "$dest"
  rsync -a \
    --exclude '.venv/' \
    --exclude 'node_modules/' \
    --exclude '__pycache__/' \
    --exclude '.pytest_cache/' \
    --exclude 'dist/' \
    --exclude '.git/' \
    --exclude '*.pyc' \
    --exclude 'host.env' \
    --exclude 'tuna.env' \
    --exclude '.env' \
    --exclude '**/host.env' \
    --exclude '**/tuna.env' \
    --exclude '**/.env' \
    --exclude 'data/ops/' \
    --exclude '**/ops/' \
    "$src/" "$dest/"
}

mkdir -p "$stage/apps"
copy_tree "$root/apps/api" "$stage/apps/api"
copy_tree "$root/apps/web" "$stage/apps/web"
copy_tree "$root/deploy" "$stage/deploy"
if [[ -d "$root/scripts" ]]; then
  copy_tree "$root/scripts" "$stage/scripts"
fi
[[ -f "$root/README.md" ]] && cp "$root/README.md" "$stage/README.md"

python3 - <<PY
from pathlib import Path
import sys
sys.path.insert(0, "$root/apps/api/src")
from robopark_api.services.ops.archives import KIND_RELEASE, build_archive
payload = Path("$stage")
data = build_archive(kind=KIND_RELEASE, source_root=payload, app_version="0.1.0")
Path("$out").write_bytes(data)
print("wrote", "$out", "bytes", len(data))
PY
