from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from pathlib import Path

import pytest
from robopark_ota import OtaError, verify_ota
from robopark_ota.verify import validate_member_name


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest(
    declared: dict[str, bytes],
    *,
    max_expanded_bytes: int = 32 * 1024 * 1024,
    entries: list[dict[str, object]] | None = None,
) -> bytes:
    document = {
        "format_version": 1,
        "app_version": "0.3.0",
        "git_sha": "a" * 40,
        "migration_head": "0036_audit_remediation_state",
        "compatible_from": ["0.2.0-rc.6"],
        "required_free_bytes": 64 * 1024 * 1024,
        "max_expanded_bytes": max_expanded_bytes,
        "changes": ["Единый OTA"],
        "files": entries
        if entries is not None
        else [
            {"path": name, "size": len(data), "sha256": _digest(data)}
            for name, data in sorted(declared.items())
        ],
    }
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _bundle(
    tmp_path: Path,
    actual: dict[str, bytes] | None = None,
    *,
    declared: dict[str, bytes] | None = None,
    manifest_bytes: bytes | None = None,
    member_modes: dict[str, int] | None = None,
    compression: int = zipfile.ZIP_STORED,
    duplicate: str | None = None,
) -> Path:
    actual = actual or {
        "__main__.py": b"print('menu')\n",
        "release/VERSION": b"0.3.0\n",
    }
    declared = actual if declared is None else declared
    target = tmp_path / "robopark-0.3.0.ota"
    with zipfile.ZipFile(target, "w", compression=compression) as archive:
        archive.writestr("manifest.json", manifest_bytes or _manifest(declared))
        for name, data in actual.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = compression
            info.create_system = 3
            mode = (member_modes or {}).get(name, stat.S_IFREG | 0o644)
            info.external_attr = mode << 16
            archive.writestr(info, data)
        if duplicate is not None:
            archive.writestr(duplicate, actual[duplicate])
    return target


def test_valid_bundle_returns_typed_metadata_and_whole_file_digest(tmp_path: Path):
    bundle = _bundle(tmp_path)

    verified = verify_ota(bundle)

    assert verified.path == bundle
    assert verified.size == bundle.stat().st_size
    assert verified.sha256 == _digest(bundle.read_bytes())
    assert verified.manifest.app_version == "0.3.0"
    assert tuple(item.path for item in verified.manifest.files) == (
        "__main__.py",
        "release/VERSION",
    )


@pytest.mark.parametrize(
    ("actual", "declared", "error"),
    [
        (
            {"__main__.py": b"changed", "release/VERSION": b"0.3.0\n"},
            {"__main__.py": b"original", "release/VERSION": b"0.3.0\n"},
            "ota_hash_mismatch",
        ),
        (
            {"__main__.py": b"print(1)", "release/VERSION": b"0.3.0\n"},
            {"__main__.py": b"print(1)"},
            "ota_manifest_invalid",
        ),
        (
            {"__main__.py": b"print(1)"},
            {"__main__.py": b"print(1)", "release/VERSION": b"0.3.0\n"},
            "ota_manifest_invalid",
        ),
    ],
)
def test_payload_inventory_and_hashes_are_enforced(
    tmp_path: Path, actual: dict[str, bytes], declared: dict[str, bytes], error: str
):
    with pytest.raises(OtaError, match=f"^{error}$"):
        verify_ota(_bundle(tmp_path, actual, declared=declared))


def test_declared_size_is_enforced(tmp_path: Path):
    data = b"print(1)"
    entry = {"path": "__main__.py", "size": len(data) + 1, "sha256": _digest(data)}
    manifest = _manifest({"__main__.py": data}, entries=[entry])

    with pytest.raises(OtaError, match="^ota_hash_mismatch$"):
        verify_ota(_bundle(tmp_path, {"__main__.py": data}, manifest_bytes=manifest))


def test_expected_whole_file_digest_is_enforced(tmp_path: Path):
    with pytest.raises(OtaError, match="^ota_hash_mismatch$"):
        verify_ota(_bundle(tmp_path), expected_sha256="0" * 64)


def test_duplicate_member_is_rejected(tmp_path: Path):
    with pytest.warns(UserWarning, match="Duplicate name"):
        bundle = _bundle(tmp_path, duplicate="release/VERSION")
    with pytest.raises(OtaError, match="^ota_invalid_container$"):
        verify_ota(bundle)


@pytest.mark.parametrize(
    "name",
    [
        "/absolute",
        "../escape",
        "release/../../escape",
        "release\\VERSION",
        "C:/windows",
        "release//VERSION",
        "release/./VERSION",
        "release/VERSION/",
        "",
        "name\x00suffix",
    ],
)
def test_unsafe_member_names_are_rejected(name: str):
    with pytest.raises(OtaError, match="^ota_invalid_container$"):
        validate_member_name(name)


@pytest.mark.parametrize("mode", [stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o644])
def test_non_regular_payload_members_are_rejected(tmp_path: Path, mode: int):
    with pytest.raises(OtaError, match="^ota_invalid_container$"):
        verify_ota(
            _bundle(
                tmp_path,
                {"__main__.py": b"print(1)"},
                member_modes={"__main__.py": mode},
            )
        )


def test_per_file_limit_is_enforced_before_reading(tmp_path: Path):
    data = b"x" * 1025
    with pytest.raises(OtaError, match="^ota_invalid_container$"):
        verify_ota(_bundle(tmp_path, {"__main__.py": data}), max_file_bytes=1024)


def test_manifest_expanded_limit_is_enforced(tmp_path: Path):
    data = b"x" * 1024
    manifest = _manifest({"__main__.py": data}, max_expanded_bytes=512)
    with pytest.raises(OtaError, match="^ota_manifest_invalid$"):
        verify_ota(_bundle(tmp_path, {"__main__.py": data}, manifest_bytes=manifest))


def test_member_count_limit_is_enforced(tmp_path: Path):
    actual = {f"release/file-{index}": b"x" for index in range(3)}
    with pytest.raises(OtaError, match="^ota_invalid_container$"):
        verify_ota(_bundle(tmp_path, actual), max_members=3)


def test_compression_ratio_limit_is_enforced(tmp_path: Path):
    actual = {"release/zeros": b"0" * 50_000}
    with pytest.raises(OtaError, match="^ota_invalid_container$"):
        verify_ota(
            _bundle(tmp_path, actual, compression=zipfile.ZIP_DEFLATED),
            max_compression_ratio=5,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda doc: doc.update(format_version=2),
        lambda doc: doc.update(app_version="../bad"),
        lambda doc: doc.update(git_sha="not-a-sha"),
        lambda doc: doc.update(changes=[]),
        lambda doc: doc.update(extra="forbidden"),
    ],
)
def test_manifest_schema_is_strict(tmp_path: Path, mutation):
    actual = {"__main__.py": b"print(1)"}
    document = json.loads(_manifest(actual))
    mutation(document)
    manifest = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()

    with pytest.raises(OtaError, match="^ota_manifest_invalid$"):
        verify_ota(_bundle(tmp_path, actual, manifest_bytes=manifest))
