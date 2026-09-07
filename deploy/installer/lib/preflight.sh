preflight() {
    ROBOPARK_ROOT=${ROBOPARK_ROOT:-/}
    case "$ROBOPARK_ROOT" in /*) ;; *) die invalid_root ;; esac
    if [ "$ROBOPARK_ROOT" != / ]; then
        [ "${ROBOPARK_TESTING:-0}" = 1 ] || die root_override_forbidden
    fi
    [ "$(id -u)" = 0 ] || die root_required
    [ -d "$ROBOPARK_ROOT" ] && [ -w "$ROBOPARK_ROOT" ] || die root_not_writable
    ROBOPARK_ARCH=$(uname -m)
    case "$ROBOPARK_ARCH" in
        aarch64|arm64) APT_ARCH=arm64 ;;
        x86_64|amd64) APT_ARCH=amd64 ;;
        *) die unsupported_arch ;;
    esac
    [ -r "$ROBOPARK_ROOT/etc/os-release" ] || die unsupported_os
    # The OS release file is root-owned host metadata, never the answer file.
    ID= ID_LIKE= VERSION_CODENAME= UBUNTU_CODENAME=
    . "$ROBOPARK_ROOT/etc/os-release"
    case "$ID" in
        debian|ubuntu) APT_OS=$ID ;;
        armbian)
            case " $ID_LIKE " in
                *' ubuntu '*) APT_OS=ubuntu ;;
                *' debian '*) APT_OS=debian ;;
                *) die unsupported_os ;;
            esac ;;
        *) die unsupported_os ;;
    esac
    APT_CODENAME=${UBUNTU_CODENAME:-$VERSION_CODENAME}
    case "$APT_CODENAME" in ''|*[!a-z0-9-]*) die unsupported_os_release ;; esac
    for prerequisite in apt-get dpkg systemctl flock; do
        command -v "$prerequisite" >/dev/null 2>&1 || die missing_prerequisite
    done
    [ -d "$ROBOPARK_ROOT/run/systemd/system" ] || die systemd_required
    free_kib=$(df -Pk "$ROBOPARK_ROOT" | awk 'NR==2 {print $4}')
    case "$free_kib" in ''|*[!0-9]*) die disk_check_failed ;; esac
    [ "$free_kib" -ge 6291456 ] || die insufficient_disk
    ROBOPARK_ETC=${ROBOPARK_ROOT%/}/etc/robopark
    ROBOPARK_VAR=${ROBOPARK_ROOT%/}/var/lib/robopark
    ROBOPARK_OPT=${ROBOPARK_ROOT%/}/opt/robopark
    for host_directory in "$ROBOPARK_ETC" "$ROBOPARK_VAR" "$ROBOPARK_OPT" "$ROBOPARK_VAR/ops" "$ROBOPARK_VAR/ops/state" "$ROBOPARK_OPT/releases"; do
        [ ! -L "$host_directory" ] || die symlinked_host_directory
    done
}
