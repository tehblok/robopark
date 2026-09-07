retry_network() {
    # One initial attempt followed by at most three bounded network retries.
    if "$@" >/dev/null 2>&1; then return 0; fi
    for retry_delay in 2 5 10; do
        sleep "$retry_delay"
        if "$@" >/dev/null 2>&1; then return 0; fi
    done
    die package_network_failed
}

install_packages() {
    audit_output=$(dpkg --audit 2>/dev/null) || die dpkg_audit_failed
    if [ -n "$audit_output" ]; then
        dpkg --configure -a >/dev/null 2>&1 || die dpkg_repair_failed
    fi
    retry_network apt-get -o DPkg::Lock::Timeout=120 update
    retry_network apt-get -o DPkg::Lock::Timeout=120 install -y ca-certificates curl gnupg jq rsync python3 python3-cryptography
    apt_etc=${ROBOPARK_ROOT%/}/etc/apt
    mkdir -p "$apt_etc/keyrings" "$apt_etc/sources.list.d"
    chmod 755 "$apt_etc/keyrings" "$apt_etc/sources.list.d"
    for repository in docker tuna; do
        case "$repository" in
            docker) repo_key_url="https://download.docker.com/linux/$APT_OS/gpg" ;;
            tuna) repo_key_url=https://repo.tuna.am/apt/gpg.key ;;
        esac
        key_tmp=$(mktemp "$apt_etc/keyrings/.$repository.XXXXXX")
        retry_network curl --fail --silent --show-error --location --proto '=https' --connect-timeout 15 --max-time 120 --output "$key_tmp" "$repo_key_url"
        gpg --batch --yes --dearmor --output "$key_tmp.gpg" "$key_tmp" >/dev/null 2>&1 || die repository_key_invalid
        chmod 644 "$key_tmp.gpg"
        mv -f "$key_tmp.gpg" "$apt_etc/keyrings/$repository.gpg"
        rm -f "$key_tmp"
    done
    source_tmp=$(mktemp "$apt_etc/sources.list.d/.docker.XXXXXX")
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/%s %s stable\n' "$APT_ARCH" "$APT_OS" "$APT_CODENAME" >"$source_tmp"
    chmod 644 "$source_tmp"
    mv -f "$source_tmp" "$apt_etc/sources.list.d/docker.list"
    source_tmp=$(mktemp "$apt_etc/sources.list.d/.tuna.XXXXXX")
    printf '%s\n' 'deb [signed-by=/etc/apt/keyrings/tuna.gpg] https://repo.tuna.am/apt/ /' >"$source_tmp"
    chmod 644 "$source_tmp"
    mv -f "$source_tmp" "$apt_etc/sources.list.d/tuna.list"
    retry_network apt-get -o DPkg::Lock::Timeout=120 update
    retry_network apt-get -o DPkg::Lock::Timeout=120 install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin tuna.am
    docker compose version >/dev/null 2>&1 || die compose_unavailable
    tuna help >/dev/null 2>&1 || die tuna_unavailable
}
