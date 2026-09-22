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
INSTALL_STATE="${ROOT%/}/var/lib/robopark/ops/state/install.json"

usage() {
    cat <<'EOF'
Robopark: простой установщик и обслуживание
  ./START.sh              автоопределение или меню обслуживания
  ./START.sh status       показать систему, состояние и версии
  ./START.sh install      установить или корректно обновить уже установленную версию
  ./START.sh update       локально обновить из этого архива
  ./START.sh reinstall    переустановить системные файлы без удаления данных
  ./START.sh repair       диагностика и безопасное восстановление
  ./START.sh diagnose     алиас команды repair
  ./START.sh remove       удалить приложение, сохранив данные
  ./START.sh remove --purge-data  удалить приложение и локальные данные
  ./START.sh clean-install полностью удалить локальные данные и установить заново
EOF
}

ACTION=${1:-}
[ "$ACTION" != --help ] && [ "$ACTION" != -h ] || { usage; exit 0; }

if [ -z "$ACTION" ] && [ -t 0 ]; then
    printf '%s\n' 'Выберите действие:'
    printf '%s\n' \
        '  1 — установить или обновить' \
        '  2 — переустановить без удаления данных' \
        '  3 — восстановить' \
        '  4 — удалить приложение, сохранив данные' \
        '  5 — показать состояние' \
        '  6 — удалить ВСЁ и установить заново (данные не восстановить)' \
        '  0 — выход'
    printf 'Номер: '
    IFS= read -r answer || exit 1
    case "$answer" in
        1) ACTION=install ;; 2) ACTION=reinstall ;; 3) ACTION=repair ;;
        4) ACTION=remove ;; 5) ACTION=status ;; 6) ACTION=clean-install ;;
        0) exit 0 ;;
        *) printf '%s\n' 'Неизвестное действие.' >&2; exit 2 ;;
    esac
fi

if [ "$(id -u)" != 0 ]; then
    command -v sudo >/dev/null 2>&1 || { printf '%s\n' 'Не найден sudo.' >&2; exit 1; }
    printf '%s\n' 'Для настройки системы потребуется пароль sudo.'
    exec sudo -- "$INSTALLER_DIR/START.sh" "$@"
fi

STATE=unknown
INSTALLED_VERSION=unknown
BUNDLE_VERSION=unknown
RELATION=unknown
HOST_OS=unknown
HOST_ARCH=unknown

detect_lifecycle() {
    lifecycle_output=$(python3 -I "$INSTALLER_DIR/lib/lifecycle.py" "$ROOT" "$INSTALLER_DIR") || {
        printf '%s\n' 'Не удалось определить состояние системы.' >&2
        exit 1
    }
    while IFS='=' read -r lifecycle_key lifecycle_value; do
        case "$lifecycle_key" in
            STATE) STATE=$lifecycle_value ;;
            INSTALLED_VERSION) INSTALLED_VERSION=$lifecycle_value ;;
            BUNDLE_VERSION) BUNDLE_VERSION=$lifecycle_value ;;
            RELATION) RELATION=$lifecycle_value ;;
            OS) HOST_OS=$lifecycle_value ;;
            ARCH) HOST_ARCH=$lifecycle_value ;;
        esac
    done <<EOF
$lifecycle_output
EOF
}

state_label() {
    case "$STATE" in
        absent) printf '%s' 'не установлена' ;;
        installed) printf '%s' 'установлена' ;;
        incomplete) printf '%s' 'установка не завершена' ;;
        damaged) printf '%s' 'повреждена или установлена частично' ;;
        removed-data) printf '%s' 'удалена, данные сохранены' ;;
        *) printf '%s' 'неизвестно' ;;
    esac
}

print_status() {
    printf '%s\n' "Система: $HOST_OS ($HOST_ARCH)"
    printf '%s\n' "Robopark: $(state_label)"
    [ "$INSTALLED_VERSION" = none ] || printf '%s\n' "Установленная версия: $INSTALLED_VERSION"
    printf '%s\n' "Версия в архиве: $BUNDLE_VERSION"
    case "$RELATION" in
        older) printf '%s\n' 'Доступно обновление.' ;;
        same) printf '%s\n' 'Совпадает с архивом.' ;;
        newer) printf '%s\n' 'На хосте установлена более новая версия.' ;;
    esac
}

has_software_state() {
    [ "$STATE" = installed ] || [ "$STATE" = incomplete ] || [ "$STATE" = damaged ]
}

refuse_downgrade() {
    printf '%s\n' "Откат версии запрещён: на хосте $INSTALLED_VERSION, в архиве $BUNDLE_VERSION." >&2
    printf '%s\n' 'Используйте архив той же или более новой версии.' >&2
    exit 1
}

if [ "$ACTION" != clean-install ]; then
    detect_lifecycle
fi

if [ "$ACTION" = status ]; then
    print_status
    exit 0
fi

if [ -z "$ACTION" ]; then
    print_status
    if [ "$STATE" = incomplete ]; then
        if [ "$RELATION" = older ]; then
            printf '%s\n' 'Найдена незавершённая старая установка — продолжаю через безопасное обновление.'
            ACTION=update
        else
            printf '%s\n' 'Найдена незавершённая установка — автоматически продолжаю её.'
            ACTION=reinstall
        fi
    elif [ "$STATE" = absent ] || [ "$STATE" = removed-data ]; then
        ACTION=install
    elif [ "$STATE" = damaged ]; then
        ACTION=repair
    else
        case "$RELATION" in older) ACTION=update ;; same) ACTION=repair ;; newer) refuse_downgrade ;; *) ACTION=repair ;; esac
    fi
fi

run_install() {
    started_at=$(date +%s)
    printf '\n%s\n' '========================================'
    printf 'Robopark: %s\n' "$ACTION"
    printf 'Начало: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')"
    printf '%s\n' 'Окно можно оставить открытым: ниже всегда виден текущий этап.'
    printf '%s\n\n' '========================================'
    set --
    if [ -f "$PRESET" ]; then
        chown 0:0 "$PRESET"
        chmod 600 "$PRESET"
        set -- --defaults "$PRESET"
    fi
    if [ -f "$INSTALL_STATE" ] && ! grep -q '"status":"complete"' "$INSTALL_STATE"; then
        set -- --resume "$@"
    fi
    if [ "$ACTION" = reinstall ]; then
        set -- --repair-existing "$@"
    fi
    if ! "$INSTALLER_DIR/install.sh" "$@"; then
        printf '\n%s\n' 'Первая попытка завершилась ошибкой. Повторяю незавершённый этап…' >&2
        if ! "$INSTALLER_DIR/install.sh" --resume "$@"; then
            printf '\n%s\n' 'Установка остановлена после двух попыток.' >&2
            printf '%s\n' 'Скопируйте строки от «Runtime bootstrap failed» до «Код:» — в них теперь есть точная категория сбоя.' >&2
            printf '%s\n' 'После исправления снова запустите этот START.sh: готовые этапы не повторятся.' >&2
            exit 1
        fi
    fi
    rm -f "$PRESET"
    elapsed=$(($(date +%s) - started_at))
    printf '\n%s\n' '========================================'
    printf 'Установка завершена за %s сек.\n' "$elapsed"
    printf '%s\n' 'Адрес: https://robopark.ru.tuna.am' 'Автозапуск: включён' 'Диагностика и обновления: включены'
    printf '%s\n' '========================================'
}

run_local_update() {
    if [ "$STATE" = absent ] || [ "$STATE" = removed-data ]; then
        ACTION=install
        run_install
        return
    fi
    [ "$RELATION" != newer ] || refuse_downgrade
    [ "$RELATION" = older ] || [ "$RELATION" = same ] || {
        printf '%s\n' 'Не удалось надёжно определить установленную версию. Сначала запустите repair.' >&2
        exit 1
    }
    started_at=$(date +%s)
    printf '\n%s\n' '========================================'
    printf '%s\n' 'Robopark: локальное OTA-обновление'
    printf '%s\n' 'Проверяю архив и готовлю изолированную сборку. Данные и настройки сохраняются.'
    if ! python3 -I "$INSTALLER_DIR/lib/local-update.py" "$ROOT" "$INSTALLER_DIR"; then
        printf '\n%s\n' 'ОБНОВЛЕНИЕ НЕ УСТАНОВЛЕНО: рабочая версия сохранена.' >&2
        printf '%s\n' "Диагностика: ${ROOT%/}/var/log/robopark/ota-update.log" >&2
        exit 1
    fi
    elapsed=$(($(date +%s) - started_at))
    printf '\n%s\n' '========================================'
    printf 'Локальное обновление завершено за %s сек.\n' "$elapsed"
    printf '%s\n' 'База, пользователи, настройки интеграций и Tuna сохранены.'
    printf '%s\n' '========================================'
}

diagnose() {
    if [ -x "$INSTALLED_HOST" ] && [ "$STATE" = installed ]; then
        if python3 -I "$INSTALLED_HOST" doctor; then
            return
        fi
        printf '%s\n' 'Диагностика нашла исправимую проблему. Запускаю безопасное восстановление…'
        python3 -I "$INSTALLED_HOST" repair
        python3 -I "$INSTALLED_HOST" doctor
        return
    fi
    if [ "$STATE" = absent ] || [ "$STATE" = removed-data ]; then
        printf '%s\n' 'Robopark ещё не установлен. Запускаю установку.'
        ACTION=install
        run_install
    elif [ "$RELATION" = newer ]; then
        refuse_downgrade
    elif [ "$RELATION" = older ]; then
        printf '%s\n' "Установка $(state_label). Обновляю её до $BUNDLE_VERSION."
        run_local_update
    else
        printf '%s\n' "Установка $(state_label). Восстанавливаю системные файлы."
        ACTION=reinstall
        run_install
    fi
}

remove_app() {
    if [ "$STATE" = absent ]; then
        printf '%s\n' 'Robopark уже удалён.'
        return
    fi
    purge=${1:-0}
    case "$ROOT" in /*) ;; *) printf '%s\n' 'Код: unsafe_data_root' >&2; exit 1 ;; esac
    if [ "$ROOT" != / ] && [ "${ROBOPARK_TESTING:-0}" != 1 ]; then
        printf '%s\n' 'Код: unsafe_data_root' >&2
        exit 1
    fi
    removal_var="${ROOT%/}/var/lib/robopark"
    if [ -e "$removal_var" ] || [ -L "$removal_var" ]; then
        python3 -I "$INSTALLER_DIR/lib/configure.py" --validate-clean-data-root "$removal_var" "$ROOT" >/dev/null 2>&1 || {
            printf '%s\n' 'Код: unsafe_data_root' >&2
            exit 1
        }
    fi
    removal_lock_dir="${ROOT%/}/run/lock/robopark"
    [ ! -L "$removal_lock_dir" ] || { printf '%s\n' 'Код: host_busy' >&2; exit 1; }
    mkdir -p "$removal_lock_dir"
    chmod 700 "$removal_lock_dir"
    exec 9>"$removal_lock_dir/install.lock"
    flock -n 9 || { printf '%s\n' 'Код: installer_locked' >&2; exit 1; }
    exec 8>"$removal_lock_dir/host.lock"
    flock -n 8 || { printf '%s\n' 'Код: host_busy' >&2; exit 1; }
    if [ -d "$removal_var/ops" ] && [ ! -L "$removal_var/ops" ]; then
        exec 7>"$removal_var/ops/install.lock"
        flock -n 7 || { printf '%s\n' 'Код: installer_locked' >&2; exit 1; }
        exec 6>"$removal_var/ops/host.lock"
        flock -n 6 || { printf '%s\n' 'Код: host_busy' >&2; exit 1; }
        for pending in \
            "$removal_var/ops/state/maintenance.json" \
            "$removal_var/ops/public/maintenance.json" \
            "$removal_var/ops/state/command-request.json" \
            "$removal_var/ops/inbox/approved.json" \
            "$removal_var/ops/state/update-worker-request.json"
        do
            [ ! -e "$pending" ] && [ ! -L "$pending" ] || {
                printf '%s\n' 'Код: host_busy' >&2
                exit 1
            }
        done
        python3 -I "$INSTALLER_DIR/lib/configure.py" --check-clean-host-state "$removal_var" >/dev/null 2>&1 || {
            printf '%s\n' 'Код: host_busy' >&2
            exit 1
        }
    fi
    if [ -t 0 ]; then
        printf 'Для удаления приложения введите УДАЛИТЬ: '
        read -r confirmation
        [ "$confirmation" = 'УДАЛИТЬ' ] || { printf '%s\n' 'Удаление отменено.'; exit 1; }
    else
        printf '%s\n' 'Для удаления требуется интерактивный терминал.' >&2
        exit 1
    fi
    systemctl stop robopark-commands.path robopark-commands.service robopark-tuna.service robopark.service robopark-updater.service 2>/dev/null || :
    if [ -d "$removal_var/ops" ]; then
        removal_busy=0
        for pending in \
            "$removal_var/ops/state/maintenance.json" \
            "$removal_var/ops/public/maintenance.json" \
            "$removal_var/ops/state/command-request.json" \
            "$removal_var/ops/inbox/approved.json" \
            "$removal_var/ops/state/update-worker-request.json"
        do
            [ ! -e "$pending" ] && [ ! -L "$pending" ] || removal_busy=1
        done
        python3 -I "$INSTALLER_DIR/lib/configure.py" --check-clean-host-state "$removal_var" >/dev/null 2>&1 || removal_busy=1
        if [ "$removal_busy" = 1 ]; then
            systemctl start --no-block robopark.service robopark-tuna.service robopark-updater.service robopark-commands.path >/dev/null 2>&1 || :
            printf '%s\n' 'Код: host_busy' >&2
            exit 1
        fi
    fi
    systemctl disable robopark.service robopark-tuna.service robopark-updater.service robopark-update-check.timer robopark-doctor.timer robopark-watchdog.timer robopark-commands.path 2>/dev/null || :
    compose="${ROOT%/}/var/lib/robopark/ops/state/current-compose.json"
    [ ! -f "$compose" ] || docker compose --project-name robopark --file "$compose" down --remove-orphans || :
    # A damaged installation may have lost its compose projection while the
    # three fixed-name runtime containers are still alive.
    docker rm --force robopark-web-1 robopark-api-1 robopark-db-1 2>/dev/null || :
    rm -rf "${ROOT%/}/opt/robopark" "${ROOT%/}/etc/robopark"
    rm -f "${ROOT%/}/etc/systemd/system"/robopark*.service "${ROOT%/}/etc/systemd/system"/robopark*.timer "${ROOT%/}/etc/systemd/system"/robopark*.path
    systemctl daemon-reload
    if [ "$purge" = 1 ]; then
        printf 'Данные и резервные материалы будут удалены. Введите УДАЛИТЬ ДАННЫЕ: '
        read -r confirmation
        [ "$confirmation" = 'УДАЛИТЬ ДАННЫЕ' ] || {
            printf '%s\n' 'Данные сохранены.'
            [ "$ACTION" != clean-install ] || exit 1
            return
        }
        if docker volume inspect robopark_robopark_postgres >/dev/null 2>&1; then
            docker volume rm robopark_robopark_postgres >/dev/null 2>&1 || {
                printf '%s\n' 'Код: database_volume_removal_failed' >&2
                exit 1
            }
        fi
        python3 -I "$INSTALLER_DIR/lib/configure.py" --validate-clean-data-root "$removal_var" "$ROOT" >/dev/null 2>&1 || {
            printf '%s\n' 'Код: unsafe_data_root' >&2
            exit 1
        }
        rm -rf "${ROOT%/}/var/lib/robopark" "${ROOT%/}/var/log/robopark"
        printf '%s\n' 'Robopark и локальные данные удалены.'
        return
    fi
    printf '%s\n' 'Robopark удалён. Локальные данные сохранены, если отдельно не подтверждено их удаление.'
}

case "$ACTION" in
    clean-install)
        python3 -I "$INSTALLER_DIR/lib/verify-bundle.py" "$INSTALLER_DIR" || {
            printf '%s\n' 'Код: release_verification_failed. Удаление не начато.' >&2
            exit 1
        }
        remove_app 1
        exec 6>&- 7>&- 8>&- 9>&-
        ACTION=install
        run_install ;;
    install)
        [ "$RELATION" != newer ] || refuse_downgrade
        case "$STATE:$RELATION" in
            absent:*|removed-data:*) run_install ;;
            incomplete:older) ACTION=update; run_local_update ;;
            incomplete:*) ACTION=reinstall; run_install ;;
            *:older) printf '%s\n' "Обнаружена версия $INSTALLED_VERSION. Выполняю обновление до $BUNDLE_VERSION."; ACTION=update; run_local_update ;;
            *:same) ACTION=reinstall; printf '%s\n' "Переустанавливаю системные файлы версии $BUNDLE_VERSION."; run_install ;;
            *:newer) refuse_downgrade ;;
            *) printf '%s\n' 'Состояние версии неизвестно. Используйте remove или архив текущей версии.' >&2; exit 1 ;;
        esac ;;
    reinstall)
        [ "$RELATION" != newer ] || refuse_downgrade
        case "$STATE:$RELATION" in
            incomplete:older) ACTION=update; run_local_update ;;
            absent:*|removed-data:*|incomplete:*) run_install ;;
            *:older) printf '%s\n' "Сначала обновляю $INSTALLED_VERSION до $BUNDLE_VERSION."; ACTION=update; run_local_update ;;
            *:same) printf '%s\n' "Переустанавливаю системные файлы версии $BUNDLE_VERSION."; run_install ;;
            *:newer) refuse_downgrade ;;
            *) printf '%s\n' 'Версия повреждённой установки неизвестна; безопасная переустановка невозможна.' >&2; exit 1 ;;
        esac ;;
    update) run_local_update ;;
    repair|diagnose) diagnose ;;
    remove) [ "${2:-}" = --purge-data ] && remove_app 1 || remove_app 0 ;;
    *) usage; exit 2 ;;
esac
