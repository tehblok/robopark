from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest
from ota_contract_cases import Manifest, write_ota
from robopark_api.services.ops.ota_uploads import (
    OtaUploadError,
    OtaUploadStore,
    _inspect_package,
)
from robopark_ota import OtaError, verify_ota


def _set(key: str, value: object) -> Callable[[Manifest], None]:
    return lambda manifest: manifest.__setitem__(key, value)


def _reverse_files(manifest: Manifest) -> None:
    manifest["files"] = list(reversed(manifest["files"]))  # type: ignore[arg-type]


MALFORMED_MANIFESTS = [
    pytest.param(
        None,
        {"__main__.py": b"print('ota')\n", ".": b"unsafe\n"},
        id="dot-member",
    ),
    pytest.param(_set("git_sha", "not-a-sha"), None, id="git-sha-format"),
    pytest.param(_set("migration_head", "../migration"), None, id="migration-format"),
    pytest.param(_set("changes", []), None, id="changes-empty"),
    pytest.param(_set("changes", [{}]), None, id="changes-item-type"),
    pytest.param(_set("changes", ["x" * 501]), None, id="changes-item-length"),
    pytest.param(
        _set("compatible_from", ["0.2.0-rc.27", "0.2.0-rc.27"]),
        None,
        id="compatible-duplicate",
    ),
    pytest.param(_set("compatible_from", [{}]), None, id="compatible-item-type"),
    pytest.param(
        _set("compatible_from", [f"0.2.0-rc.{index}" for index in range(257)]),
        None,
        id="compatible-length",
    ),
    pytest.param(_set("requirements", None), None, id="requirements-type"),
    pytest.param(
        _set(
            "requirements",
            {
                "python": ">=3.10",
                "systems": ["ubuntu", "armbian"],
                "architectures": ["aarch64", "x86_64"],
                "memory_profiles_mb": [8192, 32768, 65536],
            },
        ),
        None,
        id="requirements-exact-values",
    ),
    pytest.param(_reverse_files, None, id="files-order"),
    pytest.param(
        None,
        {"__main__.py": b"print('ota')\n", "C:/payload": b"unsafe\n"},
        id="windows-drive-member",
    ),
]


def test_valid_manifest_metadata_matches_standalone_verifier(tmp_path: Path):
    bundle = write_ota(tmp_path / "valid.ota")

    api_metadata = _inspect_package(bundle)
    standalone = verify_ota(bundle).manifest

    assert api_metadata == (
        standalone.app_version,
        standalone.changes,
        standalone.compatible_from,
        standalone.required_free_bytes,
    )
    assert api_metadata[1] == ("OTA contract parity",)


@pytest.mark.parametrize(("mutate", "payloads"), MALFORMED_MANIFESTS)
def test_api_and_standalone_reject_same_malformed_contracts(
    tmp_path: Path,
    mutate: Callable[[Manifest], None] | None,
    payloads: dict[str, bytes] | None,
):
    bundle = write_ota(tmp_path / "malformed.ota", payloads=payloads, mutate=mutate)

    with pytest.raises(
        OtaError, match="^ota_(manifest_invalid|invalid_container)$"
    ) as standalone_error:
        verify_ota(bundle)
    with pytest.raises(
        OtaUploadError, match="^ota_(manifest_invalid|invalid_container)$"
    ) as api_error:
        _inspect_package(bundle)
    assert str(api_error.value) == str(standalone_error.value)


def test_store_rejects_malformed_manifest_before_host_staging(tmp_path: Path):
    bundle = write_ota(
        tmp_path / "malformed.ota",
        mutate=_set("git_sha", {"unexpected": "object"}),
    )
    content = bundle.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    store = OtaUploadStore(
        tmp_path / "state",
        tmp_path / "host",
        chunk_bytes=len(content),
    )
    upload = store.create(
        actor_id=7,
        filename="malformed.ota",
        size=len(content),
        sha256=digest,
    )
    store.append(upload.upload_id, actor_id=7, offset=0, chunk=content)

    with pytest.raises(OtaUploadError, match="^ota_manifest_invalid$"):
        store.finalize(upload.upload_id, actor_id=7)

    assert not (store.host_root / f"{upload.upload_id}.ota").exists()


def test_unsupported_zip_compression_is_a_controlled_container_error(tmp_path: Path):
    bundle = write_ota(tmp_path / "unsupported-compression.ota")
    data = bytearray(bundle.read_bytes())
    # The synthetic archive's first local and central entries are __main__.py.
    # Change only their method fields; offsets/CRC/sizes and manifest stay valid.
    for signature, offset in ((b"PK\x03\x04", 8), (b"PK\x01\x02", 10)):
        start = data.index(signature)
        data[start + offset : start + offset + 2] = (99).to_bytes(2, "little")
    bundle.write_bytes(data)

    with pytest.raises(OtaError, match="^ota_invalid_container$"):
        verify_ota(bundle)
    with pytest.raises(OtaUploadError, match="^ota_invalid_container$"):
        _inspect_package(bundle)
