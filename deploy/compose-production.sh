#!/bin/sh
set -eu
umask 077
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

[ "$(id -u)" = 0 ] || {
    printf '%s\n' 'compose-production.sh must run as root (use sudo).' >&2
    exit 1
}

DEPLOY_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
[ -f "$DEPLOY_DIR/host.env" ] && [ ! -L "$DEPLOY_DIR/host.env" ] || {
    printf '%s\n' 'Create deploy/host.env before starting production Compose.' >&2
    exit 1
}

python3 -I "$DEPLOY_DIR/compose_secrets.py" >/dev/null
exec env HOST_ENV_FILE="$DEPLOY_DIR/host.env" \
    docker compose \
    --env-file /etc/robopark/compose-secrets.env \
    --project-directory "$DEPLOY_DIR" \
    --file "$DEPLOY_DIR/docker-compose.yml" \
    "$@"
