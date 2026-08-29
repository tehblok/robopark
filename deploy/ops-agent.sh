#!/bin/sh
# Host helper: copy a tested release from /data/ops/staging onto the checkout, then rebuild api+web.
# Runs as the compose `ops-agent` service (root + docker.sock). Does not rebuild itself.
set -eu

OPS_ROOT=/data/ops
JOB_FLAG="$OPS_ROOT/rebuild.requested"
RESULT_FILE="$OPS_ROOT/rebuild.result"
HOST_REPO=/host-repo
COMPOSE_FILE="$HOST_REPO/deploy/docker-compose.yml"

write_result() {
  job_id="$1"
  ok="$2"
  err="${3:-}"
  # Sanitize fields so rebuild.result stays valid JSON.
  job_id=$(printf '%s' "$job_id" | tr -cd 'A-Za-z0-9._-')
  err=$(printf '%s' "$err" | tr -cd 'A-Za-z0-9._-')
  if [ "$ok" = "true" ]; then
    printf '{"job_id":"%s","ok":true}\n' "$job_id" > "$RESULT_FILE"
  else
    printf '{"job_id":"%s","ok":false,"error":"%s"}\n' "$job_id" "${err:-unknown}" > "$RESULT_FILE"
  fi
}

echo "robopark ops-agent watching $JOB_FLAG"
while true; do
  if [ -f "$JOB_FLAG" ] && [ -f "$COMPOSE_FILE" ]; then
    JOB_ID=$(sed -n '1p' "$JOB_FLAG" | tr -d '\r')
    SRC=$(sed -n '2p' "$JOB_FLAG" | tr -d '\r')
    echo "ops-agent: rebuild job=${JOB_ID:-?} from ${SRC:-unknown}"

    case "$SRC" in
      "$OPS_ROOT"/staging/*) ;;
      *)
        echo "ops-agent: refuse SRC outside $OPS_ROOT/staging"
        write_result "${JOB_ID:-unknown}" false "unsafe_src"
        rm -f "$JOB_FLAG"
        sleep 4
        continue
        ;;
    esac

    if [ ! -d "$SRC" ]; then
      write_result "${JOB_ID:-unknown}" false "staging_missing"
      rm -f "$JOB_FLAG"
      sleep 4
      continue
    fi

    # Never overwrite live secrets from a release ZIP.
    if command -v rsync >/dev/null 2>&1; then
      rsync -a \
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
        "$SRC"/ "$HOST_REPO"/
    else
      # Busybox fallback: copy then purge any secret-looking files from the tree.
      cp -a "$SRC"/. "$HOST_REPO"/
      find "$HOST_REPO" \( \
          -name 'host.env' -o \
          -name 'tuna.env' -o \
          -name '.env' -o \
          -name '.env.*' \
        \) ! -name '*.env.example' -type f -delete 2>/dev/null || true
    fi

    if [ -n "${HOST_ENV_FILE:-}" ]; then
      export HOST_ENV_FILE
    else
      export HOST_ENV_FILE="$HOST_REPO/deploy/host.env"
    fi

    if docker compose -f "$COMPOSE_FILE" up -d --build api web; then
      # Result may land after the new API has already started; API reconciles on
      # /ops/maintenance and /admin/ops/job polls (and on lifespan).
      write_result "$JOB_ID" true
      echo "ops-agent: rebuild finished ok"
    else
      write_result "$JOB_ID" false "compose_failed"
      echo "ops-agent: rebuild failed"
    fi
    rm -f "$JOB_FLAG"
  fi
  sleep 4
done
