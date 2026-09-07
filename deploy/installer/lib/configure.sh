configure_host() {
    # Secrets travel only through file reads or the terminal inside this process.
    python3 "$INSTALLER_DIR/lib/configure.py" "$ROBOPARK_ROOT" "$MODE" "$CONFIG_FILE" "$RESUME" || die configuration_failed
}
