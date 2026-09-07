install_release() {
    python3 "$INSTALLER_DIR/lib/install-release.py" "$ROBOPARK_ROOT" "$INSTALLER_DIR" || die release_failed
}
