#!/usr/bin/env bash
# Local demo stack for Robopark: API + web with real network egress so that
# Tracker (Startrek) and Emergency integrations actually work. Deliberately
# does NOT touch Tuna — public HTTPS is a host-deploy concern.
#
# Usage:
#   scripts/dev-demo.sh            start both services (default, detached)
#   scripts/dev-demo.sh start      same
#   scripts/dev-demo.sh stop       stop both services
#   scripts/dev-demo.sh restart    stop then start
#   scripts/dev-demo.sh status     show listener + tail of both logs
#   scripts/dev-demo.sh run-api    run API in the foreground (does not return)
#   scripts/dev-demo.sh run-web    run web in the foreground (does not return)
#
# Notes:
# * Requires DEV_SEED=true in apps/api/.env (see docs/DEV-ACCOUNTS.md).
# * Clears HTTP(S)_PROXY so background jobs (blocker_history, emergency_keepalive)
#   can reach st-api.yandex-team.ru / Emergency directly — a proxy inherited
#   from the calling shell would otherwise return 403 and spam the logs.
# * Web dev server (Vite) listens on http://localhost:5173, proxied to the API
#   at http://127.0.0.1:8000.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_DIR="$REPO_ROOT/apps/api"
WEB_DIR="$REPO_ROOT/apps/web"
API_LOG="$API_DIR/logs/uvicorn.out"
WEB_LOG="$WEB_DIR/logs/vite.out"
API_PORT=8000
WEB_PORT=5173

log()  { printf '\033[1;36m[demo]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[demo]\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31m[demo]\033[0m %s\n' "$*" >&2; exit 1; }

kill_port() {
    local port="$1" label="$2"
    local pids
    pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
    if [[ -n "$pids" ]]; then
        log "stopping $label (pid $pids on :$port)"
        kill $pids 2>/dev/null || true
        sleep 1
        pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
        if [[ -n "$pids" ]]; then
            kill -9 $pids 2>/dev/null || true
        fi
    fi
}

wait_http() {
    local url="$1" label="$2" attempts="${3:-40}"
    for _ in $(seq 1 "$attempts"); do
        if curl -fsS -o /dev/null -m 1 "$url"; then
            log "$label ready ($url)"
            return 0
        fi
        sleep 0.5
    done
    return 1
}

require_dev_seed() {
    if [[ ! -f "$API_DIR/.env" ]]; then
        fail "missing $API_DIR/.env — copy from apps/api/.env.example"
    fi
    if ! rg -q '^DEV_SEED=true' "$API_DIR/.env"; then
        warn "DEV_SEED is not enabled in apps/api/.env — demo accounts will be missing"
    fi
}

start() {
    require_dev_seed

    [[ -x "$API_DIR/.venv/bin/uvicorn" ]] || fail "missing $API_DIR/.venv — run: cd apps/api && python3 -m venv .venv && .venv/bin/pip install -e ."
    [[ -d "$WEB_DIR/node_modules" ]]     || fail "missing $WEB_DIR/node_modules — run: cd apps/web && npm ci"

    mkdir -p "$(dirname "$API_LOG")" "$(dirname "$WEB_LOG")"

    kill_port "$API_PORT" "stale API"
    kill_port "$WEB_PORT" "stale web"

    unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy NO_PROXY no_proxy SOCKS_PROXY SOCKS5_PROXY GIT_HTTP_PROXY GIT_HTTPS_PROXY

    log "starting API on 127.0.0.1:$API_PORT"
    (
        cd "$API_DIR"
        nohup .venv/bin/uvicorn robopark_api.main:app \
            --host 127.0.0.1 --port "$API_PORT" --log-level info \
            >"$API_LOG" 2>&1 </dev/null &
        disown
    )

    log "starting web (vite) on 127.0.0.1:$WEB_PORT"
    (
        cd "$WEB_DIR"
        nohup npm run dev -- --host 127.0.0.1 --port "$WEB_PORT" \
            >"$WEB_LOG" 2>&1 </dev/null &
        disown
    )

    if ! wait_http "http://127.0.0.1:$API_PORT/health" "API" 60; then
        warn "API health did not come up — last 40 lines:"
        tail -40 "$API_LOG" >&2 || true
        exit 1
    fi
    if ! wait_http "http://127.0.0.1:$WEB_PORT" "web" 60; then
        warn "Web dev server did not come up — last 40 lines:"
        tail -40 "$WEB_LOG" >&2 || true
        exit 1
    fi

    cat <<EOF

  API  http://127.0.0.1:$API_PORT   log: $API_LOG
  Web  http://localhost:$WEB_PORT    log: $WEB_LOG

  Demo accounts (from apps/api/src/robopark_api/dev_seed.py):
    royal              RoboparkRoyal!1
    admin              RoboparkAdmin!1
    operator           RoboparkOperator!1
    mechanic           RoboparkMechanic!1
    operator_pending   RoboparkPending!1
    operator_rejected  RoboparkRejected!1

  Tuna is intentionally skipped — it belongs to host deploy (deploy/tuna-http.sh).

EOF
}

stop() {
    kill_port "$API_PORT" "API"
    kill_port "$WEB_PORT" "web"
    log "stopped"
}

status() {
    printf '\n== listeners ==\n'
    lsof -iTCP:$API_PORT -sTCP:LISTEN -nP 2>/dev/null | head -3 || true
    lsof -iTCP:$WEB_PORT -sTCP:LISTEN -nP 2>/dev/null | head -3 || true
    if [[ -f "$API_LOG" ]]; then
        printf '\n== API log (tail) ==\n'
        tail -20 "$API_LOG"
    fi
    if [[ -f "$WEB_LOG" ]]; then
        printf '\n== web log (tail) ==\n'
        tail -20 "$WEB_LOG"
    fi
}

run_api() {
    require_dev_seed
    [[ -x "$API_DIR/.venv/bin/uvicorn" ]] || fail "missing $API_DIR/.venv"
    kill_port "$API_PORT" "stale API"
    unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy NO_PROXY no_proxy SOCKS_PROXY SOCKS5_PROXY GIT_HTTP_PROXY GIT_HTTPS_PROXY
    cd "$API_DIR"
    log "API foreground on 127.0.0.1:$API_PORT (Ctrl-C to stop)"
    exec .venv/bin/uvicorn robopark_api.main:app \
        --host 127.0.0.1 --port "$API_PORT" --log-level info
}

run_web() {
    [[ -d "$WEB_DIR/node_modules" ]] || fail "missing $WEB_DIR/node_modules"
    kill_port "$WEB_PORT" "stale web"
    cd "$WEB_DIR"
    log "web foreground on 127.0.0.1:$WEB_PORT (Ctrl-C to stop)"
    exec npm run dev -- --host 127.0.0.1 --port "$WEB_PORT"
}

cmd="${1:-start}"
case "$cmd" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; start ;;
    status)  status ;;
    run-api) run_api ;;
    run-web) run_web ;;
    *)       fail "unknown command: $cmd (start|stop|restart|status|run-api|run-web)" ;;
esac
