#!/bin/sh
set -eu

cd -- "$(dirname -- "$0")"
base=$(pwd)

usage() {
  echo "Использование: ./INSTALL-AGX.sh [--operator-confirmed-platform] check|install|setup" >&2
  exit 2
}

run_privileged() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  else
    sudo "$@"
  fi
}

operator_confirmed=0
if [ "${1:-}" = "--operator-confirmed-platform" ]; then
  operator_confirmed=1
  shift
fi
action=${1:-}
case "$action" in
  check|install|setup) ;;
  *) usage ;;
esac
[ "$#" -eq 1 ] || usage

set -- robopark-*.ota
if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
  echo "Ожидается один файл robopark-<версия>.ota рядом с INSTALL-AGX.sh" >&2
  exit 2
fi
bundle="$base/$1"

command -v sha256sum >/dev/null 2>&1 || {
  echo "Нужна утилита sha256sum" >&2
  exit 2
}
sha256sum -c SHA256SUMS

verified=$(python3 "$bundle" verify --json)
expected=$(python3 -c \
  'import json, sys; value=json.loads(sys.argv[1])["version"]; print(value)' \
  "$verified")

if [ "$operator_confirmed" -eq 1 ]; then
  python3 "$base/robopark-agx.py" check --operator-confirmed-platform
else
  python3 "$base/robopark-agx.py" check
fi
if [ "$action" = check ]; then
  if [ "$operator_confirmed" -eq 1 ]; then
    echo "Платформа AGX принята по подтверждению оператора; целостность релиза проверена."
  else
    echo "Платформа GEACX1 AGX и целостность релиза проверены."
  fi
  exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
  if [ "$operator_confirmed" -eq 1 ]; then
    exec sudo "$base/INSTALL-AGX.sh" --operator-confirmed-platform "$action"
  else
    exec sudo "$base/INSTALL-AGX.sh" "$action"
  fi
fi

. "$base/nvidia-bootfw-guard.sh"
trap 'robopark_bootfw_guard_end' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
robopark_bootfw_guard_begin

if [ "$action" = install ]; then
  python3 "$bundle" install
fi

version_file=/opt/robopark/current/VERSION
if [ ! -f "$version_file" ]; then
  echo "Установка Robopark не подтверждена: нет $version_file. AGX-компоненты не изменялись." >&2
  exit 2
fi
installed=$(sed -n '1p' "$version_file")
if [ "$installed" != "$expected" ]; then
  echo "Установка Robopark не подтверждена: активна версия $installed, ожидалась $expected. AGX-компоненты не изменялись." >&2
  exit 2
fi

release=/opt/robopark/current
if [ ! -f "$release/deploy/accelerator/install.py" ]; then
  echo "В активном релизе нет установщика ускорителя. AGX-компоненты не изменялись." >&2
  exit 2
fi

# Пакеты берутся только из уже настроенных на GEACX1 репозиториев. Скрипт
# проверяет точные версии TensorRT и блокирует изменение BSP/ядра/драйверов.
if [ "$operator_confirmed" -eq 1 ]; then
  run_privileged python3 "$base/robopark-agx.py" prepare --acceleration --apply \
    --operator-confirmed-platform
  run_privileged python3 "$release/deploy/accelerator/install.py" \
    --source "$release/deploy/accelerator" --apply --start --operator-confirmed-platform
else
  run_privileged python3 "$base/robopark-agx.py" prepare --acceleration --apply
  run_privileged python3 "$release/deploy/accelerator/install.py" \
    --source "$release/deploy/accelerator" --apply --start
fi

if ! run_privileged systemctl is-active --quiet robopark-accelerator.service; then
  echo "AGX-компоненты установлены, но robopark-accelerator.service не активен." >&2
  exit 2
fi
run_privileged python3 -I \
  /opt/robopark-accelerator/current/robopark-accel status

echo "Robopark $expected и базовый AGX accelerator установлены."
echo "Модель не активирована: добавьте веса, зарегистрируйте их и выполните GPU/DLA smoke-тест."
