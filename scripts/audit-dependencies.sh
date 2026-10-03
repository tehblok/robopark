#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
repo_root=$(CDPATH= cd -P "$script_dir/.." && pwd)
audit_dir=$(mktemp -d "${TMPDIR:-/tmp}/robopark-dependency-audit.XXXXXX")
trap 'rm -rf "$audit_dir"' EXIT HUP INT TERM

cd "$repo_root/apps/web"
npm audit --audit-level=low

cd "$repo_root/apps/api"
uv export --frozen --no-dev --no-emit-project --format requirements-txt \
  --output-file "$audit_dir/api-requirements.txt" >/dev/null
for requirements in "$audit_dir/api-requirements.txt" "$repo_root/apps/bot/requirements.lock"; do
  uv tool run --from pip-audit==2.10.1 pip-audit \
    --cache-dir "$audit_dir/cache" --disable-pip --no-deps --strict \
    --requirement "$requirements"
done
