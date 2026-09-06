#!/bin/sh
# Tuna HTTP tunnel for Robopark. Sourced env comes from systemd EnvironmentFile.
# Do not rely on systemd ExecStart $VAR expansion for optional flags.
set -eu

BIND="${TUNA_BIND:-127.0.0.1:8080}"
TUNA_BIN="${TUNA_BIN:-tuna}"

i=0
if command -v curl >/dev/null 2>&1; then
  while ! curl --connect-timeout 2 --max-time 4 -fsS "http://${BIND}/api/health/ready" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -ge 60 ]; then
      echo "robopark-tuna: ${BIND}/api/health not ready" >&2
      exit 1
    fi
    sleep 2
  done
else
  echo "robopark-tuna: curl is required for readiness checks" >&2
  exit 1
fi

set -- http "$BIND" --https-redirect
if [ -n "${TUNA_DOMAIN:-}" ]; then
  set -- "$@" --domain="$TUNA_DOMAIN"
elif [ -n "${TUNA_SUBDOMAIN:-}" ]; then
  set -- "$@" --subdomain="$TUNA_SUBDOMAIN"
fi
if [ -n "${TUNA_LOCATION:-}" ]; then
  set -- "$@" --location="$TUNA_LOCATION"
fi
if [ -n "${TUNA_RATE_LIMIT:-}" ]; then
  set -- "$@" --rate-limit="$TUNA_RATE_LIMIT"
fi

exec "$TUNA_BIN" "$@"
