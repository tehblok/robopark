#!/bin/sh
# Fixed-path removal for Robopark only. No server calls and no Gatekeeper changes.
set -eu
mode=${1:-}
case "$mode" in ''|--remove-data|--plan) ;; *) echo 'Usage: uninstall.command [--remove-data|--plan]' >&2; exit 64;; esac
if [ "$mode" = --plan ]; then
  echo '/Applications/Robopark.app'
  echo '/Library/LaunchAgents/ru.robopark.agent.plist'
  echo '/Library/LaunchDaemons/ru.robopark.update-helper.plist'
  echo '/Library/PrivilegedHelperTools/RoboparkUpdater'
  echo '/Library/Application Support/RoboparkUpdater'
  echo '/Library/LaunchAgents/ru.robopark.keychain-broker.v1.plist'
  echo '/Library/Application Support/RoboparkKeychainBroker'
  echo 'Receipt: ru.robopark.agent; app data is preserved by default. Browser site data for 127.0.0.1 is never removed.'
  exit 0
fi
[ "$(/usr/bin/uname -s)" = Darwin ] || { echo 'Требуется macOS.' >&2; exit 1; }
[ "$(/usr/bin/id -u)" = 0 ] || { echo 'Удаление службы требует прав администратора.' >&2; exit 1; }
for component in '/Applications/Robopark.app' '/Library/Application Support/RoboparkUpdater' '/Library/Application Support/RoboparkKeychainBroker'; do
  [ ! -L "$component" ] || { echo 'Удаление остановлено: путь приложения заменён ссылкой.' >&2; exit 1; }
done
console_uid=$(/usr/bin/stat -f %u /dev/console)
case "$console_uid" in ''|*[!0-9]*) echo 'Не удалось определить сеанс macOS.' >&2; exit 1;; esac
# Retirement fences new runs while an in-progress installer finishes safely.
updater_root='/Library/Application Support/RoboparkUpdater'
/bin/mkdir -p "$updater_root"
case "$(/usr/bin/stat -f '%u:%Lp' "$updater_root")" in 0:700|0:755) ;; *) echo 'Небезопасные права каталога службы.' >&2; exit 1;; esac
[ "$(/usr/bin/stat -f %u "$updater_root")" = 0 ] || { echo 'Каталог службы не принадлежит администратору.' >&2; exit 1; }
[ ! -L "$updater_root/retiring" ] || { echo 'Некорректный маркер удаления.' >&2; exit 1; }
/bin/mkdir -m 700 "$updater_root/retiring" 2>/dev/null || [ -d "$updater_root/retiring" ]
[ "$(/usr/bin/stat -f '%u:%Lp' "$updater_root/retiring")" = '0:700' ] || { echo 'Некорректные права маркера удаления.' >&2; exit 1; }
# A failed/cancelled removal must allow the installed app to resume updating.
trap '/bin/rmdir "$updater_root/retiring" 2>/dev/null || true' EXIT
trap 'exit 130' HUP INT TERM
any_installer_running() {
  process_names=$(/bin/ps -axo comm=) || return 0
  /usr/bin/printf '%s\n' "$process_names" | /usr/bin/grep -Fqx '/usr/sbin/installer'
}
validate_lock() {
  [ ! -L "$1" ] && [ -d "$1" ] && [ "$(/usr/bin/stat -f '%u:%Lp' "$1")" = '0:700' ] || { echo 'Некорректная блокировка установки.' >&2; exit 1; }
}
validate_state() {
  [ ! -L "$1" ] && [ -f "$1" ] && [ "$(/usr/bin/stat -f '%u:%Lp' "$1")" = '0:600' ] && [ "$(/usr/bin/stat -f %z "$1")" -le 4096 ] || { echo 'Некорректное состояние установки.' >&2; exit 1; }
}
# Both online and local installers acquire install.lock before changing files.
# Retirement prevents a new run. A child installer can outlive its helper, so
# neither stale PID nor an absent state file alone is sufficient for recovery.
attempt=0
while :; do
  busy=0
  now=$(/bin/date +%s)
  if any_installer_running; then busy=1; fi
  for kind in install lifecycle; do
    lock="$updater_root/$kind.lock"
    if [ ! -e "$lock" ] && [ ! -L "$lock" ]; then continue; fi
    validate_lock "$lock"
    lock_busy=0
    lock_time=$(/usr/bin/stat -f %m "$lock")
    if [ "$kind" = install ]; then
      state="$lock/state"
      if [ -e "$state" ] || [ -L "$state" ]; then
        validate_state "$state"
        lock_mode=$(/usr/bin/awk -F= '$1 == "mode" { print $2 }' "$state")
        case "$lock_mode" in auto|package) ;; *) echo 'Неизвестное состояние установки.' >&2; exit 1;; esac
        if [ "$lock_mode" = auto ]; then
          helper_pid=$(/usr/bin/awk -F= '$1 == "helper_pid" { print $2 }' "$state")
          case "$helper_pid" in ''|*[!0-9]*) echo 'Не удалось проверить установщик.' >&2; exit 1;; esac
          [ "$helper_pid" -gt 1 ] || exit 1
          if /bin/kill -0 "$helper_pid" 2>/dev/null; then lock_busy=1; fi
        fi
      fi
      # Covers a crash between mkdir, state persistence and installer spawn.
      if [ $((now - lock_time)) -lt 600 ]; then lock_busy=1; fi
    else
      state="$lock/state.json"
      if [ -e "$state" ] || [ -L "$state" ]; then
        validate_state "$state"
        helper_pid=$(/usr/bin/plutil -extract helper_pid raw -o - "$state")
        installer_pid=$(/usr/bin/plutil -extract installer_pid raw -o - "$state" 2>/dev/null || true)
        started_at=$(/usr/bin/plutil -extract installer_started_at raw -o - "$state" 2>/dev/null || true)
        case "$helper_pid" in ''|*[!0-9]*) echo 'Не удалось проверить службу обновления.' >&2; exit 1;; esac
        [ "$helper_pid" -gt 1 ] || exit 1
        if /bin/kill -0 "$helper_pid" 2>/dev/null; then lock_busy=1; fi
        case "$installer_pid" in
          ''|null) ;;
          -1)
            case "$started_at" in ''|*[!0-9]*) echo 'Не удалось проверить запуск установщика.' >&2; exit 1;; esac
            if [ $((now - started_at)) -lt 600 ]; then lock_busy=1; fi ;;
          *[!0-9]*) echo 'Не удалось проверить установщик.' >&2; exit 1;;
          *) if [ "$(/bin/ps -p "$installer_pid" -o comm= 2>/dev/null | /usr/bin/xargs)" = '/usr/sbin/installer' ]; then lock_busy=1; fi ;;
        esac
      elif [ $((now - lock_time)) -lt 600 ]; then
        lock_busy=1
      fi
    fi
    if [ "$busy" = 1 ] || [ "$lock_busy" = 1 ]; then
      busy=1
    else
      # Never recurse into a lock or remove an unvalidated extra entry.
      if [ -f "$state" ]; then /bin/rm "$state"; fi
      /bin/rmdir "$lock" || { echo 'Неожиданное содержимое блокировки; удаление остановлено.' >&2; exit 1; }
    fi
  done
  [ "$busy" = 1 ] || break
  [ "$attempt" -lt 300 ] || { echo 'Установка ещё выполняется или восстанавливается. Повторите удаление через несколько минут.' >&2; exit 1; }
  /bin/sleep 1
  attempt=$((attempt + 1))
done
/bin/launchctl bootout system /Library/LaunchDaemons/ru.robopark.update-helper.plist 2>/dev/null || true
if [ "$console_uid" -ge 501 ]; then
  /bin/launchctl bootout "gui/$console_uid" /Library/LaunchAgents/ru.robopark.agent.plist 2>/dev/null || true
  /bin/launchctl bootout "gui/$console_uid/ru.robopark.keychain-broker.v1" 2>/dev/null || true
fi
/bin/rm -f /Library/LaunchAgents/ru.robopark.agent.plist /Library/LaunchDaemons/ru.robopark.update-helper.plist /Library/PrivilegedHelperTools/RoboparkUpdater
/bin/rm -f /Library/LaunchAgents/ru.robopark.keychain-broker.v1.plist
/bin/rm -rf '/Applications/Robopark.app' '/Library/Application Support/RoboparkUpdater'
/bin/rm -rf '/Library/Application Support/RoboparkKeychainBroker'
/usr/sbin/pkgutil --forget ru.robopark.agent >/dev/null 2>&1 || true
if [ "$mode" = --remove-data ] && [ "$console_uid" -ge 501 ]; then
  console_user=$(/usr/bin/id -un "$console_uid")
  target_home=$(/usr/bin/dscl . -read "/Users/$console_user" NFSHomeDirectory | /usr/bin/sed 's/^NFSHomeDirectory: //')
  case "$target_home" in /Users/*) ;; *) echo 'Приложение удалено; пользовательские данные сохранены: неизвестный домашний каталог.'; exit 0;; esac
  [ -d "$target_home" ] && [ ! -L "$target_home" ] || { echo 'Приложение удалено; данные сохранены.'; exit 0; }
  for relative in 'Library/WebKit/ru.robopark.agent' 'Library/Caches/ru.robopark.agent' 'Library/Application Support/ru.robopark.agent'; do
    [ ! -L "$target_home/$relative" ] || continue
    /bin/launchctl asuser "$console_uid" /usr/bin/sudo -u "$console_user" /bin/rm -rf "$target_home/$relative"
  done
  /bin/launchctl asuser "$console_uid" /usr/bin/sudo -u "$console_user" /usr/bin/defaults delete ru.robopark.agent >/dev/null 2>&1 || true
  # Delete only our own generic-password service, without printing keychain data.
  for service in ru.robopark.agent ru.robopark.agent.stable-v1 ru.robopark.keychain-broker-v1; do
    count=0
    while [ "$count" -lt 512 ] && /bin/launchctl asuser "$console_uid" /usr/bin/sudo -u "$console_user" /usr/bin/security delete-generic-password -s "$service" >/dev/null 2>&1; do count=$((count + 1)); done
  done
  echo 'Robopark удалён. Keychain и данные прежнего встроенного окна удалены. Данные сайта 127.0.0.1 в системном браузере сохранены; при необходимости удалите их в настройках данных сайтов браузера.'
else
  echo 'Robopark удалён. Данные приложения и сайта 127.0.0.1 в системном браузере сохранены для повторной установки.'
fi
