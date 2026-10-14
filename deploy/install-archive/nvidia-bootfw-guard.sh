#!/bin/sh
# NVIDIA r39.2.1's reviewed postinst exits before firmware work while this
# official marker exists. Keep the marker only for the current installer and
# never modify vendor maintainer scripts or dpkg state directly.

RP_BOOTFW_MARKER=/opt/nvidia/l4t-packages/.nv-l4t-disable-boot-fw-update-in-preinstall
RP_BOOTFW_POSTINST_SHA256=26e63400bd5a7b6692917e1079f61561e42119bcd25d8bbbc2342051f0148099
RP_BOOTFW_CREATED=0

robopark_bootfw_guard_end() {
    if [ "$RP_BOOTFW_CREATED" -eq 1 ]; then
        rm -f -- "$RP_BOOTFW_MARKER"
        RP_BOOTFW_CREATED=0
    fi
}

robopark_bootfw_guard_begin() {
    RP_BOOTFW_POSTINST=$(dpkg-query --control-path nvidia-l4t-bootloader postinst 2>/dev/null || true)
    if [ -z "$RP_BOOTFW_POSTINST" ]; then
        echo 'NVIDIA bootloader package отсутствует; восстановление dpkg не требуется.'
        return 0
    fi

    RP_BOOTFW_DIGEST=$(sha256sum "$RP_BOOTFW_POSTINST")
    if [ "${RP_BOOTFW_DIGEST%% *}" != "$RP_BOOTFW_POSTINST_SHA256" ]; then
        echo 'Неизвестный NVIDIA bootloader postinst; автоматический пропуск прошивки не применён.' >&2
        return 1
    fi
    if [ -L /opt/nvidia/l4t-packages ] || [ -L "$RP_BOOTFW_MARKER" ]; then
        echo 'Небезопасный путь NVIDIA boot firmware marker.' >&2
        return 1
    fi

    if [ ! -e "$RP_BOOTFW_MARKER" ]; then
        install -d -m 755 /opt/nvidia/l4t-packages
        touch "$RP_BOOTFW_MARKER"
        RP_BOOTFW_CREATED=1
    fi
    echo 'NVIDIA: на время установки обновление boot firmware пропущено штатным механизмом; загрузчик сохраняется.'

    RP_BOOTFW_PENDING=
    for RP_BOOTFW_PACKAGE in nvidia-l4t-bootloader nvidia-l4t-bsp; do
        RP_BOOTFW_STATUS=$(dpkg-query -W -f='${Status}' "$RP_BOOTFW_PACKAGE" 2>/dev/null || true)
        case "$RP_BOOTFW_STATUS" in
            'install ok half-configured'|'install ok unpacked')
                RP_BOOTFW_PENDING="$RP_BOOTFW_PENDING $RP_BOOTFW_PACKAGE"
                ;;
        esac
    done
    if [ -n "$RP_BOOTFW_PENDING" ]; then
        # The value contains only the two literal package names above.
        # shellcheck disable=SC2086
        dpkg --configure $RP_BOOTFW_PENDING
    fi
}
