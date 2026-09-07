#!/bin/sh
# Never inherit tracing or automatic exports from the invoking shell.
set +x
set +a
set -eu
umask 077
unset TUNA_TOKEN SEED_PASSWORD SECRET_KEY GITHUB_TOKEN OPERATOR_SHARED_PASSWORD
INSTALLER_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for module in common preflight packages layout configure install-release; do
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
        --help) printf '%s\n' 'sudo ./install.sh [--resume] [--non-interactive CONFIG_FILE]'; exit 0 ;;
        *) die invalid_arguments ;;
    esac
    shift
done
preflight
# Preflight performs no host writes. Keep the lock outside release directories.
mkdir -p "$ROBOPARK_VAR/ops/state"
exec 9>"$ROBOPARK_VAR/ops/install.lock"
flock -n 9 || die installer_locked
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
CURRENT_PHASE=complete
write_state complete complete
printf '%s\n' 'Установка файлов Robopark завершена. Настройка автозапуска выполняется этапом сервисов.'
