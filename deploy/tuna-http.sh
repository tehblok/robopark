#!/bin/sh
# Tuna HTTP tunnel for Robopark. Sourced env comes from systemd EnvironmentFile.
# Do not rely on systemd ExecStart $VAR expansion for optional flags.
set -eu

BIND="${TUNA_BIND:-127.0.0.1:8080}"
TUNA_BIN="${TUNA_BIN:-tuna}"

# Readiness is also checked on every service restart, including after a reboot.
[ "$BIND" = 127.0.0.1:8080 ] || { echo 'robopark-tuna: invalid local bind' >&2; exit 1; }
i=0
while ! python3 -c '
import http.client
import sys

connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=2)
try:
    connection.request("GET", "/api/health/ready")
    status = connection.getresponse().status
except (OSError, http.client.HTTPException):
    status = 0
finally:
    connection.close()
sys.exit(0 if status == 200 else 1)
' >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then
    echo 'robopark-tuna: local API not ready' >&2
    exit 1
  fi
  sleep 2
done

# Do not retain session cookies, TOTP requests or terminal frames in the inspector.
export TUNA_INSPECT=false
set -- http "$BIND" --https-redirect --inspect=false
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
