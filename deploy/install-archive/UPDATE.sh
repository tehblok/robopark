#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"
set -- robopark-*.ota
if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
  echo "Ожидается один OTA-пакет рядом с UPDATE.sh" >&2
  exit 2
fi
sha256sum -c SHA256SUMS
python3 "$1" verify --json
bundle="$(pwd)/$1"
if [ "$(id -u)" -eq 0 ]; then
  exec python3 "$bundle" update
fi
exec sudo python3 "$bundle" update
