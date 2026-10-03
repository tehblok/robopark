#!/bin/sh

set -u

stdout_file="$(mktemp "${TMPDIR:-/tmp}/robopark-npm-ci-stdout.XXXXXX")" || exit 1
stderr_file="$(mktemp "${TMPDIR:-/tmp}/robopark-npm-ci-stderr.XXXXXX")" || {
  rm -f "$stdout_file"
  exit 1
}
cleanup() {
  rm -f "$stdout_file" "$stderr_file"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

attempt=1
structured_code_pattern='^npm (error|ERR!) code [^[:space:]]+[[:space:]]*$'
transient_code_pattern='^npm (error|ERR!) code (ETIMEDOUT|EAI_AGAIN|ECONNRESET|ECONNREFUSED|ENOTFOUND|EHOSTUNREACH|ENETUNREACH|ENETRESET|ERR_SOCKET_TIMEOUT|E502|E503|E504)[[:space:]]*$'
while :; do
  npm ci --prefer-offline --no-audit --no-fund >"$stdout_file" 2>"$stderr_file"
  status=$?
  cat "$stdout_file"
  cat "$stderr_file" >&2

  if [ "$status" -eq 0 ]; then
    exit 0
  fi

  if ! grep -Eq "$transient_code_pattern" "$stdout_file" "$stderr_file"; then
    exit "$status"
  fi
  if grep -Eh "$structured_code_pattern" "$stdout_file" "$stderr_file" | grep -Eqv "$transient_code_pattern"; then
    exit "$status"
  fi
  if [ "$attempt" -ge 3 ]; then
    exit "$status"
  fi

  delay=$((attempt * 10))
  printf 'npm ci failed with a transient network error; retrying in %s seconds\n' "$delay" >&2
  sleep "$delay"
  attempt=$((attempt + 1))
done
