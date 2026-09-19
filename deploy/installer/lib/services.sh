install_services() {
    python3 -I "$INSTALLER_DIR/lib/ensure-docker.py" || die docker_not_ready
    python3 -I "$ROBOPARK_OPT/current/deploy/compose_secrets.py" \
        --directory "$ROBOPARK_ETC" --host-env "$ROBOPARK_ETC/host.env" \
        || die compose_secret_bootstrap_failed
    python3 -I "$ROBOPARK_OPT/host-tools/robopark" bootstrap-compose || die runtime_bootstrap_failed
    python3 -I "$INSTALLER_DIR/lib/install-services.py" "$ROBOPARK_ROOT" || die unit_install_failed
    systemctl daemon-reload
    systemctl enable docker.service robopark.service robopark-tuna.service robopark-updater.service \
        robopark-update-check.timer robopark-doctor.timer robopark-watchdog.timer robopark-commands.path
    for unit in docker.service robopark.service robopark-tuna.service robopark-updater.service robopark-update-check.timer robopark-doctor.timer robopark-watchdog.timer robopark-commands.path; do
        systemctl is-enabled "$unit" >/dev/null 2>&1 || die autostart_not_enabled
    done
    systemctl start robopark.service
    ready_attempt=0
    while ! curl -fsS --connect-timeout 1 --max-time 2 http://127.0.0.1:8080/api/health/ready >/dev/null 2>&1; do
        ready_attempt=$((ready_attempt + 1))
        [ "$ready_attempt" -lt 30 ] || die application_not_ready
        sleep 2
    done
    python3 -I "$INSTALLER_DIR/lib/install-trust.py" "$ROBOPARK_ROOT" || die signing_trust_failed
    systemctl start robopark-tuna.service
    systemctl is-active robopark.service robopark-tuna.service >/dev/null 2>&1 || die service_not_active
    # Tuna terminates public TLS. Require a real HTTPS response so users do not
    # discover a DNS/certificate/subdomain problem only after installation.
    TUNA_SUBDOMAIN= TUNA_DOMAIN= TUNA_LOCATION=ru
    . "$ROBOPARK_ETC/tuna.env"
    if [ -n "$TUNA_DOMAIN" ]; then
        public_origin="https://$TUNA_DOMAIN"
    else
        public_origin="https://$TUNA_SUBDOMAIN.$TUNA_LOCATION.tuna.am"
    fi
    public_attempt=0
    while ! curl -fsS --proto '=https' --tlsv1.2 --connect-timeout 3 --max-time 8 "$public_origin/" >/dev/null 2>&1; do
        public_attempt=$((public_attempt + 1))
        [ "$public_attempt" -lt 30 ] || die public_https_not_ready
        sleep 2
    done
}

start_host_automation() {
    systemctl start robopark-updater.service robopark-update-check.timer robopark-doctor.timer robopark-watchdog.timer robopark-commands.path
}
