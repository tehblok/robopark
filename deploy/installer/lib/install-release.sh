install_release() {
    if [ "${RESUME:-0}" = 1 ]; then
        python3 "$INSTALLER_DIR/lib/install-release.py" "$ROBOPARK_ROOT" "$INSTALLER_DIR" --resume-incomplete || die release_failed
    else
        python3 "$INSTALLER_DIR/lib/install-release.py" "$ROBOPARK_ROOT" "$INSTALLER_DIR" || die release_failed
    fi
}
