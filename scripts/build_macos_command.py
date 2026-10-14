#!/usr/bin/env python3
"""One downloadable file for local Mac install/update; no product downloads."""

from __future__ import annotations

import argparse
import base64
import hashlib
import re
from pathlib import Path


def build(package: Path, output: Path, *, version: str) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-rc\.\d+)?", version):
        raise ValueError("invalid_version")
    if (
        package.is_symlink()
        or not package.is_file()
        or not 4 <= package.stat().st_size <= 256 * 1024 * 1024
    ):
        raise ValueError("invalid_package")
    data = package.read_bytes()
    if not data.startswith(b"xar!"):
        raise ValueError("invalid_package")
    checksum = hashlib.sha256(data).hexdigest()
    uninstall = Path(__file__).with_name("macos_uninstall.sh").read_text()
    script = r"""#!/bin/bash
# Robopark test: one-file local installation/update, with embedded payload.
set -euo pipefail
umask 077
version='@VERSION@'
expected='@SHA@'
mode=${1:-install}
case "$mode" in install|--verify|--uninstall|--uninstall-remove-data|--help) ;; *) echo 'Неизвестный режим. Используйте --help.' >&2; exit 64;; esac
if [ "$mode" = --help ]; then
  echo "Robopark $version: запустите без параметров для установки или локального обновления."
  echo '--verify: проверить встроенный пакет без установки.'
  echo '--uninstall: удалить приложение и службы, сохранить локальные данные.'
  echo '--uninstall-remove-data: удалить также локальные данные текущего пользователя.'
  exit 0
fi
if [ "$mode" != --verify ] && [ "$(/usr/bin/uname -s)" != Darwin ]; then echo 'Требуется macOS 13+ на Apple Silicon.' >&2; exit 1; fi
stage=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/robopark-install.XXXXXX")
trap '/bin/rm -rf "$stage"' EXIT
if [ "$mode" = --uninstall ] || [ "$mode" = --uninstall-remove-data ]; then
  /bin/cat > "$stage/uninstall.sh" <<'ROBOPARK_UNINSTALL_SCRIPT'
@UNINSTALL@
ROBOPARK_UNINSTALL_SCRIPT
  echo 'Будут удалены приложение Robopark и его фоновые службы.'
  if [ "$mode" = --uninstall-remove-data ]; then echo 'Будут также удалены локальные данные, вход и офлайн-черновики текущего пользователя.'; fi
  read -r -p 'Продолжить? Введите DELETE: ' confirmation
  [ "$confirmation" = DELETE ] || exit 0
  if [ "$mode" = --uninstall-remove-data ]; then /usr/bin/sudo /bin/sh "$stage/uninstall.sh" --remove-data
  else /usr/bin/sudo /bin/sh "$stage/uninstall.sh"; fi
  exit 0
fi
if [ "$(/usr/bin/uname -s)" = Darwin ]; then
  /usr/bin/awk 'found {print} /^__ROBOPARK_PACKAGE_BELOW__$/ {found=1}' "$0" | /usr/bin/base64 -D > "$stage/Robopark.pkg"
else
  /usr/bin/awk 'found {print} /^__ROBOPARK_PACKAGE_BELOW__$/ {found=1}' "$0" | /usr/bin/base64 --decode > "$stage/Robopark.pkg"
fi
actual=$(/usr/bin/shasum -a 256 "$stage/Robopark.pkg" | /usr/bin/awk '{print $1}')
if [ "$actual" != "$expected" ]; then echo 'Ошибка SHA-256: файл повреждён. Установка отменена.' >&2; exit 1; fi
if [ "$mode" = --verify ]; then echo "VERIFIED Robopark $version SHA-256 $actual"; exit 0; fi
[ "$(/usr/bin/uname -m)" = arm64 ] || { echo 'Этот выпуск предназначен для Apple Silicon.' >&2; exit 1; }
os_major=$(/usr/bin/sw_vers -productVersion | /usr/bin/cut -d. -f1)
[ "$os_major" -ge 13 ] || { echo 'Требуется macOS 13 или новее.' >&2; exit 1; }
current=$(/usr/libexec/PlistBuddy -c 'Print :RoboparkReleaseVersion' /Applications/Robopark.app/Contents/Info.plist 2>/dev/null || true)
if [ -n "$current" ]; then
  # Stop local installers from silently downgrading a newer automatic update.
  if ! /usr/bin/awk -v old="$current" -v new="$version" 'BEGIN {
    if (old !~ /^[0-9]+\.[0-9]+\.[0-9]+(-rc\.[0-9]+)?$/) exit 2;
    split(old,a,/[.-]/); split(new,b,/[.-]/);
    for(i=1;i<=3;i++) { if(a[i]+0>b[i]+0) exit 1; if(a[i]+0<b[i]+0) exit 0; }
    if(a[4]=="" && b[4]!="") exit 1;
    if(a[4]!="" && b[4]=="") exit 0;
    exit (a[5]+0>b[5]+0) ? 1 : 0;
  }'; then echo "Уже установлена версия $current. Понижение или неизвестная версия не допускается." >&2; exit 1; fi
fi
echo "Robopark $version · тестовый сервер roboparktest.ru.tuna.am"
echo 'Установка/обновление сохраняет вход, настройки и офлайн-данные.'
echo 'macOS запросит пароль администратора для установки фоновой службы.'
/usr/bin/sudo /usr/sbin/installer -pkg "$stage/Robopark.pkg" -target /
installed=$(/usr/libexec/PlistBuddy -c 'Print :RoboparkReleaseVersion' /Applications/Robopark.app/Contents/Info.plist)
[ "$installed" = "$version" ] || { echo 'Установленная версия не совпала с ожидаемой.' >&2; exit 1; }
echo "Готово: Robopark $version. Последующие онлайн-обновления проверяются автоматически."
/usr/bin/open /Applications/Robopark.app || true
exit 0
__ROBOPARK_PACKAGE_BELOW__
"""
    script = (
        script.replace("@VERSION@", version)
        .replace("@SHA@", checksum)
        .replace("@UNINSTALL@", uninstall)
    )
    encoded = base64.encodebytes(data).decode("ascii")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(script + encoded, encoding="utf-8")
    output.chmod(0o755)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    build(args.package, args.output, version=args.version)


if __name__ == "__main__":
    main()
