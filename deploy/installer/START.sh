#!/bin/sh
# One entry point for installation and routine host maintenance.
set +x
set +a
set -eu
umask 077

INSTALLER_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=${ROBOPARK_ROOT:-/}
INSTALLED_HOST="${ROOT%/}/opt/robopark/host-tools/robopark"
PRESET="$INSTALLER_DIR/.robopark-preset.env"

usage() {
    cat <<'EOF'
Robopark: простой установщик и обслуживание
  ./START.sh              первая установка или меню обслуживания
  ./START.sh install      установить
  ./START.sh update       локально обновить из этого архива
  ./START.sh reinstall    переустановить системные файлы без удаления данных
  ./START.sh diagnose     диагностика и безопасное исправление
  ./START.sh remove       удалить приложение, сохранив данные
  ./START.sh remove --purge-data  удалить приложение и локальные данные
EOF
}

ACTION=${1:-}
[ "$ACTION" != --help ] && [ "$ACTION" != -h ] || { usage; exit 0; }

if [ "$(id -u)" != 0 ]; then
    command -v sudo >/dev/null 2>&1 || { printf '%s\n' 'Не найден sudo.' >&2; exit 1; }
    printf '%s\n' 'Для настройки системы потребуется пароль sudo.'
    exec sudo -- "$INSTALLER_DIR/START.sh" "$@"
fi

is_installed() { [ -x "$INSTALLED_HOST" ]; }

if [ -z "$ACTION" ]; then
    if ! is_installed; then
        ACTION=install
    elif [ -t 0 ]; then
        printf '%s\n' 'Robopark уже установлен. Выберите действие:'
        printf '%s\n' '  1 — локальное обновление из этого архива' '  2 — переустановка без удаления данных' '  3 — диагностика и исправление' '  4 — удаление' '  0 — выход'
        printf 'Номер: '
        read -r answer
        case "$answer" in 1) ACTION=update ;; 2) ACTION=reinstall ;; 3) ACTION=diagnose ;; 4) ACTION=remove ;; 0) exit 0 ;; *) printf '%s\n' 'Неизвестное действие.' >&2; exit 2 ;; esac
    else
        ACTION=update
    fi
fi

run_install() {
    set --
    if [ -f "$PRESET" ]; then
        chown 0:0 "$PRESET"
        chmod 600 "$PRESET"
        set -- --defaults "$PRESET"
    fi
    state="${ROOT%/}/var/lib/robopark/ops/state/install.json"
    if [ -f "$state" ] && ! grep -q '"status":"complete"' "$state"; then
        set -- --resume "$@"
    fi
    if ! "$INSTALLER_DIR/install.sh" "$@"; then
        printf '%s\n' 'Установка прервалась. Исправляю состояние и повторяю последний этап…' >&2
        "$INSTALLER_DIR/install.sh" --resume "$@"
    fi
    rm -f "$PRESET"
    printf '%s\n' 'Готово: https://robopark.ru.tuna.am'
}

diagnose() {
    is_installed || { printf '%s\n' 'Robopark ещё не установлен.' >&2; exit 1; }
    if ! python3 -I "$INSTALLED_HOST" doctor; then
        printf '%s\n' 'Диагностика нашла исправимую проблему. Запускаю безопасное восстановление…'
        python3 -I "$INSTALLED_HOST" repair
        python3 -I "$INSTALLED_HOST" doctor
    fi
}

remove_app() {
    is_installed || { printf '%s\n' 'Robopark уже удалён.'; return; }
    purge=${1:-0}
    if [ -t 0 ]; then
        printf 'Для удаления приложения введите УДАЛИТЬ: '
        read -r confirmation
        [ "$confirmation" = 'УДАЛИТЬ' ] || { printf '%s\n' 'Удаление отменено.'; exit 1; }
    else
        printf '%s\n' 'Для удаления требуется интерактивный терминал.' >&2
        exit 1
    fi
    systemctl stop robopark-tuna.service robopark.service robopark-updater.service 2>/dev/null || :
    systemctl disable robopark.service robopark-tuna.service robopark-updater.service robopark-update-check.timer robopark-doctor.timer robopark-watchdog.timer robopark-commands.path 2>/dev/null || :
    compose="${ROOT%/}/var/lib/robopark/ops/state/current-compose.json"
    [ ! -f "$compose" ] || docker compose --project-name robopark --file "$compose" down --remove-orphans || :
    rm -rf "${ROOT%/}/opt/robopark" "${ROOT%/}/etc/robopark"
    rm -f "${ROOT%/}/etc/systemd/system"/robopark*.service "${ROOT%/}/etc/systemd/system"/robopark*.timer "${ROOT%/}/etc/systemd/system"/robopark*.path
    systemctl daemon-reload
    if [ "$purge" = 1 ]; then
        printf 'Данные и резервные материалы будут удалены. Введите УДАЛИТЬ ДАННЫЕ: '
        read -r confirmation
        [ "$confirmation" = 'УДАЛИТЬ ДАННЫЕ' ] || { printf '%s\n' 'Данные сохранены.'; return; }
        rm -rf "${ROOT%/}/var/lib/robopark" "${ROOT%/}/var/log/robopark"
    fi
    printf '%s\n' 'Robopark удалён. Локальные данные сохранены, если отдельно не подтверждено их удаление.'
}

case "$ACTION" in
    install|update|reinstall) run_install ;;
    diagnose) diagnose ;;
    remove) [ "${2:-}" = --purge-data ] && remove_app 1 || remove_app 0 ;;
    *) usage; exit 2 ;;
esac
