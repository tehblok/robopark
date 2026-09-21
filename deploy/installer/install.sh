#!/bin/sh
# Never inherit tracing or automatic exports from the invoking shell.
set +x
set +a
set -eu
umask 077
unset TUNA_TOKEN SEED_PASSWORD SECRET_KEY GITHUB_TOKEN OPERATOR_SHARED_PASSWORD
INSTALLER_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for module in common preflight packages layout configure install-release services; do
    . "$INSTALLER_DIR/lib/$module.sh"
done
MODE=interactive
CONFIG_FILE=
RESUME=0
CLEAN_REINSTALL=0
REPAIR_EXISTING=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --resume) RESUME=1 ;;
        --clean-reinstall) CLEAN_REINSTALL=1 ;;
        --repair-existing) REPAIR_EXISTING=1 ;;
        --non-interactive)
            [ "$#" -ge 2 ] || die invalid_arguments
            MODE=non-interactive
            CONFIG_FILE=$2
            shift ;;
        --defaults)
            [ "$#" -ge 2 ] || die invalid_arguments
            MODE=interactive
            CONFIG_FILE=$2
            shift ;;
        --help) printf '%s\n' './START.sh или sudo ./install.sh [--resume] [--repair-existing] [--clean-reinstall] [--defaults CONFIG_FILE] [--non-interactive CONFIG_FILE]'; exit 0 ;;
        *) die invalid_arguments ;;
    esac
    shift
done
reject_pending_host_state() {
    for pending in \
        "$ROBOPARK_VAR/ops/state/maintenance.json" \
        "$ROBOPARK_VAR/ops/public/maintenance.json" \
        "$ROBOPARK_VAR/ops/state/command-request.json" \
        "$ROBOPARK_VAR/ops/inbox/approved.json" \
        "$ROBOPARK_VAR/ops/state/update-worker-request.json"
    do
        [ ! -e "$pending" ] && [ ! -L "$pending" ] || die host_busy
    done
    python3 -I "$INSTALLER_DIR/lib/configure.py" --check-clean-host-state "$ROBOPARK_VAR" || die host_busy
}
clean_host_state_is_idle() {
    for pending in \
        "$ROBOPARK_VAR/ops/state/maintenance.json" \
        "$ROBOPARK_VAR/ops/public/maintenance.json" \
        "$ROBOPARK_VAR/ops/state/command-request.json" \
        "$ROBOPARK_VAR/ops/inbox/approved.json" \
        "$ROBOPARK_VAR/ops/state/update-worker-request.json"
    do
        [ ! -e "$pending" ] && [ ! -L "$pending" ] || return 1
    done
    python3 -I "$INSTALLER_DIR/lib/configure.py" --check-clean-host-state "$ROBOPARK_VAR"
}
restore_after_aborted_clean_reinstall() {
    systemctl start --no-block robopark.service robopark-tuna.service robopark-updater.service robopark-commands.path >/dev/null 2>&1 || :
}
preflight
if [ "$CLEAN_REINSTALL" = 1 ]; then
    # Refuse an absent, unresolved or aliased target before creating locks or
    # touching containers/volumes. The expected path derives from trusted root.
    python3 -I "$INSTALLER_DIR/lib/configure.py" --validate-clean-data-root "$ROBOPARK_VAR" "$ROBOPARK_ROOT" || die unsafe_data_root
fi
# These installer-owned inodes survive deletion of /var/lib/robopark. The
# legacy in-tree locks are also held so an older running host cannot overlap a
# clean reinstall during the transition to the stable lock namespace.
ROBOPARK_LOCK_DIR=${ROBOPARK_ROOT%/}/run/lock/robopark
[ ! -L "$ROBOPARK_LOCK_DIR" ] || die unsafe_lock_directory
mkdir -p "$ROBOPARK_LOCK_DIR"
chmod 700 "$ROBOPARK_LOCK_DIR"
exec 9>"$ROBOPARK_LOCK_DIR/install.lock"
flock -n 9 || die installer_locked
exec 8>"$ROBOPARK_LOCK_DIR/host.lock"
flock -n 8 || die host_busy
mkdir -p "$ROBOPARK_VAR/ops/state"
[ ! -L "$ROBOPARK_VAR/ops/install.lock" ] || die unsafe_install_lock
exec 7>"$ROBOPARK_VAR/ops/install.lock"
flock -n 7 || die installer_locked
[ ! -L "$ROBOPARK_VAR/ops/host.lock" ] || die unsafe_host_lock
exec 6>"$ROBOPARK_VAR/ops/host.lock"
flock -n 6 || die host_busy
if [ "$CLEAN_REINSTALL" = 1 ]; then
    python3 -I "$INSTALLER_DIR/lib/configure.py" --validate-clean-data-root "$ROBOPARK_VAR" "$ROBOPARK_ROOT" || die unsafe_data_root
    reject_pending_host_state
    [ -t 0 ] && [ -r /dev/tty ] || die local_confirmation_required
    if [ "${ROBOPARK_TESTING:-0}" = 1 ]; then
        printf '\nБудет безвозвратно удалён точный data root Robopark:\n  %s\n' "$ROBOPARK_VAR"
        printf 'Введите DELETE ROBOPARK DATA: '
        IFS= read -r CLEAN_CONFIRMATION || die local_confirmation_required
    else
        printf '\nБудет безвозвратно удалён точный data root Robopark:\n  %s\n' "$ROBOPARK_VAR" >/dev/tty
        printf 'Введите DELETE ROBOPARK DATA: ' >/dev/tty
        IFS= read -r CLEAN_CONFIRMATION </dev/tty || die local_confirmation_required
    fi
    [ "$CLEAN_CONFIRMATION" = 'DELETE ROBOPARK DATA' ] || die local_confirmation_required
    # The stable and legacy locks close normal writers; repeat the durable-state
    # check immediately before the first destructive side effect.
    reject_pending_host_state
    systemctl stop robopark-commands.path robopark-updater.service robopark-tuna.service robopark.service >/dev/null 2>&1 || :
    # A durable claim may be published while systemd drains an already-running
    # worker. Recheck after quiescing and before the first database/container
    # deletion; on refusal, restore the safe service state and leave all data.
    if ! clean_host_state_is_idle; then
        restore_after_aborted_clean_reinstall
        die host_busy
    fi
    docker rm --force robopark-web-1 robopark-api-1 robopark-db-1 >/dev/null 2>&1 || :
    if docker volume inspect robopark_robopark_postgres >/dev/null 2>&1; then
        docker volume rm robopark_robopark_postgres >/dev/null 2>&1 || die clean_database_removal_failed
    fi
    python3 -I "$INSTALLER_DIR/lib/configure.py" --validate-clean-data-root "$ROBOPARK_VAR" "$ROBOPARK_ROOT" || die unsafe_data_root
    reject_pending_host_state
    python3 -I "$INSTALLER_DIR/lib/configure.py" --clean-data-root "$ROBOPARK_VAR" "$ROBOPARK_ROOT" "$CLEAN_CONFIRMATION" || die unsafe_data_root
fi
# Clean reinstall removed the old tree while the stable lock remained held.
mkdir -p "$ROBOPARK_VAR/ops/state"
if [ "$CLEAN_REINSTALL" = 1 ]; then
    reject_pending_host_state
else
    for pending in "$ROBOPARK_VAR/ops/state/maintenance.json" "$ROBOPARK_VAR/ops/public/maintenance.json" "$ROBOPARK_VAR/ops/state/command-request.json" "$ROBOPARK_VAR/ops/inbox/approved.json"; do
        [ ! -e "$pending" ] && [ ! -L "$pending" ] || die host_busy
    done
fi
CURRENT_PHASE=packages
trap 'cleanup' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if ! phase_done packages; then
    run_phase packages install_packages
else
    printf '\n[1/5] Системные пакеты уже установлены — пропускаю повторную загрузку.\n'
fi
# Always validate configuration and links, including on an already-complete rerun.
run_phase layout prepare_layout
run_phase configure configure_host
run_phase release install_release
run_phase services install_services
CURRENT_PHASE=complete
write_state complete complete
# No host/configuration mutations after ownership is released. A synchronous
# consumer start may now acquire host.lock without waiting for its parent.
exec 6>&-
exec 7>&-
exec 8>&-
exec 9>&-
start_host_automation
printf '%s\n' 'Robopark установлен, API готов, автозапуск и Tuna включены.'
