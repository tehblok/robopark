#!/bin/sh
# Host helper: copy a tested release from /data/ops/staging onto the checkout, then rebuild api+web.
# Runs as the compose `ops-agent` service (root + docker.sock). Does not rebuild itself.
set -eu

OPS_ROOT="${OPS_ROOT:-/data/ops}"
JOB_FLAG="$OPS_ROOT/rebuild.requested"
RESULT_FILE="$OPS_ROOT/rebuild.result"
HOST_REPO="${HOST_REPO:-/host-repo}"
COMPOSE_FILE="${COMPOSE_FILE:-$HOST_REPO/deploy/docker-compose.yml}"
HOST_ENV_FILE="${HOST_ENV_FILE:-$HOST_REPO/deploy/host.env}"
READY_TIMEOUT_SECONDS="${READY_TIMEOUT_SECONDS:-180}"
OPS_AGENT_COPY_MODE="${OPS_AGENT_COPY_MODE:-auto}"
MODE="${1:-watch}"

write_result() {
  job_id="$1"
  ok="$2"
  err="${3:-}"
  # Sanitize fields so rebuild.result stays valid JSON.
  job_id=$(printf '%s' "$job_id" | tr -cd 'A-Za-z0-9._-') || return 1
  err=$(printf '%s' "$err" | tr -cd 'A-Za-z0-9._-') || return 1
  if [ "$ok" = "true" ]; then
    printf '{"job_id":"%s","ok":true}\n' "$job_id" > "$RESULT_FILE" || return 1
  else
    printf '{"job_id":"%s","ok":false,"error":"%s"}\n' \
      "$job_id" "${err:-unknown}" > "$RESULT_FILE" || return 1
  fi
  return 0
}

complete_job() {
  job_id="$1"
  ok="$2"
  err="${3:-}"

  if ! write_result "$job_id" "$ok" "$err"; then
    echo "ops-agent: result_write_failed" >&2
    return 1
  fi
  if ! rm -f "$JOB_FLAG"; then
    echo "ops-agent: flag_remove_failed" >&2
    if ! write_result "$job_id" false "flag_remove_failed"; then
      echo "ops-agent: result_write_failed" >&2
      if ! rm -f "$RESULT_FILE"; then
        echo "ops-agent: result_cleanup_failed" >&2
      fi
    fi
    return 1
  fi
  return 0
}

fail_job() {
  complete_job "$1" false "$2"
}

sanitize_staging() {
  if ! find "$SRC" \
    \( -type f -o -type l \) \
    \( -name 'host.env' -o -name 'tuna.env' -o -name '.env' -o -name '.env.*' \) \
    ! -name '*.env.example' \
    -exec rm -f -- {} \;; then
    return 1
  fi

  remaining=$(find "$SRC" \
    \( -type f -o -type l \) \
    \( -name 'host.env' -o -name 'tuna.env' -o -name '.env' -o -name '.env.*' \) \
    ! -name '*.env.example' \
    -print) || return 1
  [ -z "$remaining" ]
}

process_job() {
  if [ ! -f "$JOB_FLAG" ]; then
    return 0
  fi

  JOB_ID=$(sed -n '1p' "$JOB_FLAG" | tr -d '\r')
  SRC=$(sed -n '2p' "$JOB_FLAG" | tr -d '\r')
  echo "ops-agent: rebuild job=${JOB_ID:-?} from ${SRC:-unknown}"

  if [ ! -d "$SRC" ] || [ ! -d "$OPS_ROOT/staging" ]; then
    if ! fail_job "${JOB_ID:-unknown}" "staging_missing"; then
      return 1
    fi
    return 1
  fi

  STAGING_ROOT_REAL=$(realpath "$OPS_ROOT/staging") || {
    if ! fail_job "${JOB_ID:-unknown}" "unsafe_src"; then
      return 1
    fi
    return 1
  }
  SRC_REAL=$(realpath "$SRC") || {
    if ! fail_job "${JOB_ID:-unknown}" "unsafe_src"; then
      return 1
    fi
    return 1
  }
  case "$SRC_REAL" in
    "$STAGING_ROOT_REAL"/*) SRC="$SRC_REAL" ;;
    *)
      if ! fail_job "${JOB_ID:-unknown}" "unsafe_src"; then
        return 1
      fi
      return 1
      ;;
  esac

  case "$OPS_AGENT_COPY_MODE" in
    auto)
      if command -v rsync >/dev/null 2>&1; then
        copy_mode=rsync
      else
        copy_mode=copy
      fi
      ;;
    rsync|copy) copy_mode="$OPS_AGENT_COPY_MODE" ;;
    *)
      if ! fail_job "$JOB_ID" "invalid_copy_mode"; then
        return 1
      fi
      return 1
      ;;
  esac

  if ! sanitize_staging; then
    if ! fail_job "$JOB_ID" "sanitize_failed"; then
      return 1
    fi
    return 1
  fi

  case "$copy_mode" in
    rsync)
      if ! rsync -a \
        --exclude 'host.env' \
        --exclude 'tuna.env' \
        --exclude '.env' \
        --exclude '**/host.env' \
        --exclude '**/tuna.env' \
        --exclude '**/.env' \
        --exclude '.venv/' \
        --exclude 'node_modules/' \
        --exclude '__pycache__/' \
        --exclude '.git/' \
        "$SRC"/ "$HOST_REPO"/; then
        if ! fail_job "$JOB_ID" "copy_failed"; then
          return 1
        fi
        return 1
      fi
      ;;
    copy)
      if ! cp -a "$SRC"/. "$HOST_REPO"/; then
        if ! fail_job "$JOB_ID" "copy_failed"; then
          return 1
        fi
        return 1
      fi
      ;;
  esac

  export HOST_ENV_FILE
  if docker compose --project-name robopark -f "$COMPOSE_FILE" up -d --build \
    --wait --wait-timeout "$READY_TIMEOUT_SECONDS" api web; then
    # Result may land after the new API has already started; API reconciles on
    # /ops/maintenance and /admin/ops/job polls (and on lifespan).
    if ! complete_job "$JOB_ID" true; then
      return 1
    fi
    echo "ops-agent: rebuild finished ok"
    return 0
  fi

  if ! fail_job "$JOB_ID" "compose_failed"; then
    return 1
  fi
  echo "ops-agent: rebuild failed"
  return 1
}

case "$MODE" in
  once) process_job ;;
  watch)
    echo "robopark ops-agent watching $JOB_FLAG"
    while true; do
      if ! process_job; then
        echo "ops-agent: job processing failed" >&2
      fi
      sleep 4
    done
    ;;
  *)
    echo "usage: ops-agent.sh [watch|once]" >&2
    exit 2
    ;;
esac
