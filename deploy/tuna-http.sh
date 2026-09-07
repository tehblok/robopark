#!/bin/sh
# Tuna HTTP tunnel for Robopark. Sourced env comes from systemd EnvironmentFile.
# Do not rely on systemd ExecStart $VAR expansion for optional flags.
set -eu

BIND="${TUNA_BIND:-127.0.0.1:8080}"
TUNA_BIN="${TUNA_BIN:-tuna}"

# Readiness is also checked on every service restart, including after a reboot.
[ "$BIND" = 127.0.0.1:8080 ] || { echo 'robopark-tuna: invalid local bind' >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo 'robopark-tuna: curl required' >&2; exit 1; }
i=0
while ! curl -fsS --connect-timeout 1 --max-time 2 "http://${BIND}/api/health/ready" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then
    echo 'robopark-tuna: local API not ready' >&2
    exit 1
  fi
  sleep 2
done

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
