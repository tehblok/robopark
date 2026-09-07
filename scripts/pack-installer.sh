#!/usr/bin/env bash
# Package an already signed release plus trusted installer bootstrap.
set +x
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
release=""
public_key="$root/deploy/keys/release-public-key.pem"
signing_key="${ROBOPARK_SIGNING_KEY_FILE:-}"
output=""
preset=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --release) release="${2:?missing release}"; shift 2 ;;
    --public-key) public_key="${2:?missing public key}"; shift 2 ;;
    --signing-key) signing_key="${2:?missing signing key}"; shift 2 ;;
    --preset) preset="${2:?missing preset}"; shift 2 ;;
    -*) echo 'invalid argument' >&2; exit 2 ;;
    *) [[ -z "$output" ]] || exit 2; output="$1"; shift ;;
  esac
done
: "${release:?use --release signed-release.zip}"
: "${signing_key:?set ROBOPARK_SIGNING_KEY_FILE or --signing-key}"
: "${output:?provide output.tar.gz outside the release payload directory}"
args=(--installer --release "$release" --public-key "$public_key" --signing-key "$signing_key" --output "$output")
[[ -z "$preset" ]] || args+=(--preset "$preset")
exec python3 "$root/scripts/release_pack.py" "${args[@]}"
