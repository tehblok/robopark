#!/usr/bin/env bash
# Pack signed application sources, never runtime data or signing material.
set +x
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
out="${1:-"$root/robopark-release.zip"}"
[[ $# -le 1 ]] || exit 2
version="${ROBOPARK_RELEASE_VERSION:-$(cat "$root/VERSION")}"
git_sha="$(python3 "$root/scripts/release-git-sha.py" "$root")"
: "${ROBOPARK_SIGNING_KEY_FILE:?set ROBOPARK_SIGNING_KEY_FILE to the Ed25519 PEM key}"
: "${ROBOPARK_MIGRATION_HEAD:?set ROBOPARK_MIGRATION_HEAD to the Alembic migration head}"
exec python3 "$root/scripts/release_pack.py" --repository \
  --root "$root" --output "$out" --version "$version" \
  --git-sha "$git_sha" --migration-head "$ROBOPARK_MIGRATION_HEAD" \
  --signing-key "$ROBOPARK_SIGNING_KEY_FILE"
