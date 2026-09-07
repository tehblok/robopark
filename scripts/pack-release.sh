#!/usr/bin/env bash
# Pack a Robopark *release* ZIP (application tree), not a data snapshot.
# Usage: scripts/pack-release.sh [output.zip]
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
out="${1:-"$root/robopark-release.zip"}"
version="${ROBOPARK_RELEASE_VERSION:-0.1.0}"
git_sha="$(python3 "$root/scripts/release-git-sha.py" "$root")"
: "${ROBOPARK_SIGNING_KEY_FILE:?set ROBOPARK_SIGNING_KEY_FILE to the Ed25519 PEM key}"
: "${ROBOPARK_MIGRATION_HEAD:?set ROBOPARK_MIGRATION_HEAD to the Alembic migration head}"
test -r "$ROBOPARK_SIGNING_KEY_FILE"
stage="$(mktemp -d "${TMPDIR:-/tmp}/robopark-release.XXXX")"
cleanup() { rm -rf "$stage"; }
trap cleanup EXIT

copy_tree() {
  local src="$1" dest="$2"
  shift 2
  mkdir -p "$dest"
  rsync -a "$@" \
    --exclude '.venv/' \
    --exclude 'node_modules/' \
    --exclude '__pycache__/' \
    --exclude '.pytest_cache/' \
    --exclude '.ruff_cache/' \
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
    "$src/" "$dest/"
}

mkdir -p "$stage/apps"
copy_tree "$root/apps/api" "$stage/apps/api" --exclude '/data/'
# Application databases, WAL/SHM files and uploads never belong to a release.
seed="$root/apps/api/data/emergency_sections.json"
[[ -f "$seed" && ! -L "$seed" ]] || { echo 'versioned API seed is missing' >&2; exit 1; }
mkdir -p "$stage/apps/api/data"
cp "$seed" "$stage/apps/api/data/emergency_sections.json"
copy_tree "$root/apps/web" "$stage/apps/web"
copy_tree "$root/deploy" "$stage/deploy"
if [[ -d "$root/scripts" ]]; then
  copy_tree "$root/scripts" "$stage/scripts"
fi
# Root metadata is an explicit allowlist; never copy root env/key files.
for metadata in README.md VERSION .dockerignore .gitignore .github/workflows/ci.yml; do
  if [[ -f "$root/$metadata" && ! -L "$root/$metadata" ]]; then
    mkdir -p "$(dirname "$stage/$metadata")"
    cp "$root/$metadata" "$stage/$metadata"
  fi
done

python3 "$root/scripts/release_pack.py" \
  --root "$stage" --output "$out" --version "$version" \
  --git-sha "$git_sha" --migration-head "$ROBOPARK_MIGRATION_HEAD" \
  --signing-key "$ROBOPARK_SIGNING_KEY_FILE"
