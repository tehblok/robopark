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
while [ "$#" -gt 0 ]; do
    case "$1" in
        --resume) RESUME=1 ;;
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
        --help) printf '%s\n' './START.sh или sudo ./install.sh [--resume] [--defaults CONFIG_FILE] [--non-interactive CONFIG_FILE]'; exit 0 ;;
        *) die invalid_arguments ;;
    esac
    shift
done
preflight
# Preflight performs no host writes. Keep the lock outside release directories.
mkdir -p "$ROBOPARK_VAR/ops/state"
exec 9>"$ROBOPARK_VAR/ops/install.lock"
flock -n 9 || die installer_locked
[ ! -L "$ROBOPARK_VAR/ops/host.lock" ] || die unsafe_host_lock
exec 8>"$ROBOPARK_VAR/ops/host.lock"
flock -n 8 || die host_busy
for pending in "$ROBOPARK_VAR/ops/state/maintenance.json" "$ROBOPARK_VAR/ops/public/maintenance.json" "$ROBOPARK_VAR/ops/state/command-request.json" "$ROBOPARK_VAR/ops/inbox/approved.json"; do
    [ ! -e "$pending" ] && [ ! -L "$pending" ] || die host_busy
done
CURRENT_PHASE=packages
trap 'cleanup' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if ! phase_done packages; then
    run_phase packages install_packages
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
exec 8>&-
exec 9>&-
start_host_automation
printf '%s\n' 'Robopark установлен, API готов, автозапуск и Tuna включены.'
