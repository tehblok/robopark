die() {
    printf 'Ошибка установки: %s\n' "$1" >&2
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
    write_state "$CURRENT_PHASE" running
    # Do not use `if function` here: POSIX shells disable errexit in that context.
    "$@"
    if [ "$CURRENT_PHASE" = packages ]; then PACKAGES_COMPLETE=true; fi
    write_state "$CURRENT_PHASE" complete
}

cleanup() {
    install_exit=$?
    trap - EXIT
    if [ "$install_exit" -ne 0 ]; then
        write_state "$CURRENT_PHASE" failed || :
        printf 'Этап %s прерван; повторите установку с --resume.\n' "$CURRENT_PHASE" >&2
    fi
    exit "$install_exit"
}
