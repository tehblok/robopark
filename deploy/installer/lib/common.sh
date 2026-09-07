die() {
    case "$1" in
        runtime_bootstrap_failed) hint='Не удалось подготовить контейнеры. Причина Docker указана строкой выше.' ;;
        docker_not_ready) hint='Docker не запустился после повторных попыток.' ;;
        insufficient_disk) hint='Недостаточно свободного места: требуется минимум 6 GiB.' ;;
        package_network_failed) hint='Не удалось скачать системные пакеты после повторных попыток.' ;;
        public_https_not_ready) hint='Локальный Robopark готов, но публичный HTTPS Tuna ещё недоступен.' ;;
        *) hint="$1" ;;
    esac
    printf 'ОШИБКА: %s\nКод: %s\n' "$hint" "$1" >&2
    exit 1
}

write_state() {
    # Only constant phase/status tokens enter this journal, never wizard data.
    state_tmp=$(mktemp "$ROBOPARK_VAR/ops/state/.install.XXXXXX") || return 1
    printf '{"phase":"%s","status":"%s","packages_complete":%s}\n' \
        "$1" "$2" "$PACKAGES_COMPLETE" >"$state_tmp"
    chmod 600 "$state_tmp"
    # GNU sync -f flushes the containing filesystem without requiring Python
    # before the package phase has installed it.
    sync -f "$state_tmp" || return 1
    mv -f "$state_tmp" "$ROBOPARK_VAR/ops/state/install.json" || return 1
    sync -f "$ROBOPARK_VAR/ops/state"
}

phase_done() {
    PACKAGES_COMPLETE=false
    if [ -f "$ROBOPARK_VAR/ops/state/install.json" ] &&
        grep -q '"packages_complete":true' "$ROBOPARK_VAR/ops/state/install.json"; then
        PACKAGES_COMPLETE=true
        return 0
    fi
    return 1
}

run_phase() {
    CURRENT_PHASE=$1
    shift
    case "$CURRENT_PHASE" in
        packages) phase_number=1; phase_label='Системные пакеты, Docker и Tuna' ;;
        layout) phase_number=2; phase_label='Каталоги и права доступа' ;;
        configure) phase_number=3; phase_label='Настройки Robopark' ;;
        release) phase_number=4; phase_label='Проверка и установка релиза' ;;
        services) phase_number=5; phase_label='Запуск контейнеров и служб' ;;
        *) phase_number='?'; phase_label="$CURRENT_PHASE" ;;
    esac
    phase_started=$(date +%s)
    printf '\n[%s/5] %s — начато в %s\n' "$phase_number" "$phase_label" "$(date '+%H:%M:%S')"
    write_state "$CURRENT_PHASE" running
    # Do not use `if function` here: POSIX shells disable errexit in that context.
    "$@"
    if [ "$CURRENT_PHASE" = packages ]; then PACKAGES_COMPLETE=true; fi
    write_state "$CURRENT_PHASE" complete
    phase_elapsed=$(($(date +%s) - phase_started))
    printf '[%s/5] Готово за %s сек.\n' "$phase_number" "$phase_elapsed"
}

cleanup() {
    install_exit=$?
    trap - EXIT
    if [ "$install_exit" -ne 0 ]; then
        write_state "$CURRENT_PHASE" failed || :
        printf 'Этап %s прерван. START.sh повторит его автоматически; при новом запуске прогресс сохранится.\n' "$CURRENT_PHASE" >&2
    fi
    exit "$install_exit"
}
