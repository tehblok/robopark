"""Shared storage support, including before the first installed host release."""

from __future__ import annotations

import json
import sys
from pathlib import Path

__all__ = [
    "TARGETS",
    "StorageError",
    "choose_install_storage",
    "inspect_storage",
    "load_layout",
    "plan_storage",
    "prepare_storage",
    "prepared_empty_target",
    "refresh_storage_guard",
    "require_storage",
    "validate_removal_mounts",
]

try:
    from robopark_storage.storage_layout import (
        TARGETS,
        StorageError,
        inspect_storage,
        load_layout,
        require_storage,
    )
    from robopark_storage.storage_setup import (
        plan_storage,
        prepare_storage,
        refresh_storage_guard,
    )
except ModuleNotFoundError as exc:
    if exc.name != "robopark_storage":
        raise
    # Source checkout only. Executable OTA archives contain the independent
    # package above and must not import an older installed host module.
    from robopark_host.storage_layout import (
        TARGETS,
        StorageError,
        inspect_storage,
        load_layout,
        require_storage,
    )
    from robopark_host.storage_setup import (
        plan_storage,
        prepare_storage,
        refresh_storage_guard,
    )


def prepared_empty_target(root: Path, target: Path, status: dict) -> bool:
    return (
        status.get("state") == "ready"
        and target in {root / path.lstrip("/") for path in TARGETS.values()}
        and not target.is_symlink()
        and target.is_dir()
        and not any(target.iterdir())
    )


def validate_removal_mounts(root: Path, targets: tuple[Path, ...]) -> None:
    if load_layout(root) is None:
        return
    inventory = inspect_storage(root)
    for row in inventory["mounts"]:
        mounted = root / row["target"].lstrip("/")
        for target in targets:
            if mounted != target and mounted.is_relative_to(target):
                raise StorageError("storage_nested_mount")


def choose_install_storage(root: Path) -> None:
    """Offer layouts on real Linux NVMe hosts; keep legacy hosts unchanged."""
    existing = load_layout(root)
    if existing is not None:
        if existing["state"] == "ready":
            # Reconcile watchdog activation if power was lost immediately
            # after persisting ready. The manifest records prior approval.
            prepare_storage(
                existing["mode"],
                existing["device"],
                confirmation=f"PREPARE {existing['mode']} {existing['uuid']}",
                root=root,
            )
            return
        mode, device = existing["mode"], existing["device"]
    else:
        if root != Path("/") or sys.platform != "linux":
            return
        inventory = inspect_storage(root)
        disks = [
            row
            for row in inventory["block_devices"]
            if row["path"].startswith("/dev/nvme")
        ]
        if not disks:
            return
        print("\nРазмещение Robopark:")
        print("  1 — ОС и рабочие данные на NVMe")
        print("  2 — ОС на eMMC, рабочие данные на NVMe")
        print("  3 — Сохранить текущую разметку без управления NVMe")
        print("  0 — Отмена")
        choice = input("Выберите вариант: ").strip()
        if choice == "3":
            return
        if choice not in {"1", "2"}:
            raise StorageError("confirmation_required")
        mode = "nvme-root" if choice == "1" else "emmc-nvme-data"
        device = None
        if mode == "emmc-nvme-data":
            for row in disks:
                if row["type"] == "part":
                    print(
                        f"  {row['path']} · {row.get('fstype') or 'без ФС'} · UUID {row.get('uuid') or 'нет'}"
                    )
            device = input(
                "Пустой раздел ext4 на NVMe (например /dev/nvme0n1p1): "
            ).strip()
    plan = plan_storage(mode, device, root=root)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    print(
        "Разделы не форматируются. Docker/containerd должны быть остановлены заранее."
    )
    confirmation = input(f"Для подготовки введите {plan['confirmation']}: ").strip()
    prepare_storage(mode, device, confirmation=confirmation, root=root)
