#!/usr/bin/env bash
# Pack signed application sources, never runtime data or signing material.
set +x
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
metadata="$root/deploy/release-metadata.json"
if [[ "${1:-}" == --metadata ]]; then
  [[ $# -ge 2 ]] || exit 2
  metadata="$2"
  shift 2
fi
out="${1:-"$root/robopark-release.zip"}"
[[ $# -le 1 ]] || exit 2
version="${ROBOPARK_RELEASE_VERSION:-$(cat "$root/VERSION")}"
git_sha="$(python3 "$root/scripts/release-git-sha.py" "$root")"
: "${ROBOPARK_SIGNING_KEY_FILE:?set ROBOPARK_SIGNING_KEY_FILE to the Ed25519 PEM key}"
exec python3 "$root/scripts/release_pack.py" --repository \
  --root "$root" --output "$out" --version "$version" \
  --git-sha "$git_sha" --metadata "$metadata" \
  --signing-key "$ROBOPARK_SIGNING_KEY_FILE"
