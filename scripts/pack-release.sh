#!/usr/bin/env bash
# Pack a Robopark *release* ZIP (application tree), not a data snapshot.
# Usage: scripts/pack-release.sh [output.zip]
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
out="${1:-"$root/robopark-release.zip"}"
version="${ROBOPARK_RELEASE_VERSION:-0.1.0}"
git_sha="$(git -C "$root" rev-parse HEAD)"
: "${ROBOPARK_SIGNING_KEY_FILE:?set ROBOPARK_SIGNING_KEY_FILE to the Ed25519 PEM key}"
test -r "$ROBOPARK_SIGNING_KEY_FILE"
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

python3 "$root/scripts/release_pack.py" \
  --root "$stage" --output "$out" --version "$version" \
  --git-sha "$git_sha" --signing-key "$ROBOPARK_SIGNING_KEY_FILE"
