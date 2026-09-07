install_services() {
    python3 -I "$INSTALLER_DIR/lib/ensure-docker.py" || die docker_not_ready
    python3 -I "$ROBOPARK_OPT/host-tools/robopark" bootstrap-compose || die runtime_bootstrap_failed
    python3 -I "$INSTALLER_DIR/lib/install-services.py" "$ROBOPARK_ROOT" || die unit_install_failed
    systemctl daemon-reload
    systemctl enable docker.service robopark.service robopark-tuna.service robopark-updater.service \
        robopark-update-check.timer robopark-doctor.timer robopark-watchdog.timer
    systemctl start robopark.service
    ready_attempt=0
    while ! curl -fsS --connect-timeout 1 --max-time 2 http://127.0.0.1:8080/api/health/ready >/dev/null 2>&1; do
        ready_attempt=$((ready_attempt + 1))
        [ "$ready_attempt" -lt 30 ] || die application_not_ready
        sleep 2
    done
    systemctl start robopark-tuna.service
    systemctl start robopark-updater.service robopark-update-check.timer robopark-doctor.timer robopark-watchdog.timer
}
