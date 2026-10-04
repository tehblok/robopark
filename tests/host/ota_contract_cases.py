from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from collections.abc import Callable
from pathlib import Path

Payloads = dict[str, bytes]
Manifest = dict[str, object]


def manifest_for(payloads: Payloads) -> Manifest:
    return {
        "format_version": 1,
        "app_version": "0.3.0",
        "git_sha": "a" * 40,
        "migration_head": "0054_ota_contract_parity",
        "compatible_from": ["0.2.0-rc.27", "0.2.0-rc.28"],
        "required_free_bytes": 64 * 1024 * 1024,
        "max_expanded_bytes": 32 * 1024 * 1024,
        "changes": ["  OTA contract parity  "],
        "requirements": {
            "python": ">=3.10",
            "systems": ["armbian", "ubuntu"],
            "architectures": ["aarch64", "x86_64"],
            "memory_profiles_mb": [8192, 32768, 65536],
        },
        "files": [
            {
                "path": name,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            for name, data in sorted(payloads.items())
        ],
    }


def write_ota(
    path: Path,
    *,
    payloads: Payloads | None = None,
    mutate: Callable[[Manifest], None] | None = None,
) -> Path:
    payloads = payloads or {
        "__main__.py": b"print('ota')\n",
        "release/VERSION": b"0.3.0\n",
    }
    manifest = manifest_for(payloads)
    if mutate is not None:
        mutate(manifest)
    raw = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in payloads.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, data)
        archive.writestr("manifest.json", raw)
    return path
