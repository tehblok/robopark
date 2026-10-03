#!/bin/sh
set -eu

cd -- "$(dirname -- "$0")"
set -- robopark-*.ota
if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
  echo "Ожидается один файл robopark-<версия>.ota рядом с INSTALL.sh" >&2
  exit 2
fi

command -v sha256sum >/dev/null 2>&1 || {
  echo "Нужна утилита sha256sum" >&2
  exit 2
}
sha256sum -c SHA256SUMS
python3 "$1" verify --json

bundle="$(pwd)/$1"
if [ "$(id -u)" -eq 0 ]; then
  exec python3 "$bundle"
fi
exec sudo python3 "$bundle"
