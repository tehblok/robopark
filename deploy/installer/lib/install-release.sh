install_release() {
    repair_argument=
    [ "${REPAIR_EXISTING:-0}" != 1 ] || repair_argument=--repair-existing
    if [ "${RESUME:-0}" = 1 ]; then
        python3 "$INSTALLER_DIR/lib/install-release.py" "$ROBOPARK_ROOT" "$INSTALLER_DIR" --resume-incomplete $repair_argument || die release_failed
    else
        python3 "$INSTALLER_DIR/lib/install-release.py" "$ROBOPARK_ROOT" "$INSTALLER_DIR" $repair_argument || die release_failed
    fi
}
