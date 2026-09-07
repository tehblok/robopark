"""Format-2 release archives are signed, canonical, and structurally safe."""

from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed448 import Ed448PrivateKey
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from robopark_api.services.ops.archives import (
    KIND_RELEASE,
    ArchiveError,
    build_archive,
    inspect_archive,
    unpack_archive,
)
from robopark_api.services.ops.release_signing import (
    canonical_manifest_bytes,
    sign_manifest,
    verify_manifest_signature,
)


@pytest.fixture
def ed25519_keys() -> tuple[bytes, bytes]:
    private = Ed25519PrivateKey.generate()
    return (
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ),
    )


def release_tree(tmp_path: Path) -> Path:
    root = tmp_path / "release"
    (root / "apps" / "api").mkdir(parents=True)
    (root / "apps" / "api" / "main.py").write_text("VERSION = '1.2.3'\n", encoding="utf-8")
    return root


def build_signed_release(tmp_path: Path, keys: tuple[bytes, bytes]) -> bytes:
    return build_archive(
        kind=KIND_RELEASE,
        source_root=release_tree(tmp_path),
        app_version="1.2.3",
        release_meta={"git_sha": "a" * 40, "migration_head": "0017_driver_work_reports"},
        signing_key=keys[0],
    )


def tamper_manifest(archive: bytes, key: str, value: object) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(archive))
    target = io.BytesIO()
    with source, zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "manifest.json":
                manifest = json.loads(data)
                manifest[key] = value
                data = json.dumps(manifest, ensure_ascii=False).encode()
            zf.writestr(info.filename, data)
    return target.getvalue()


def append_member(archive: bytes, name: str, data: bytes) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(archive))
    target = io.BytesIO()
    with source, zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for info in source.infolist():
            zf.writestr(info.filename, source.read(info.filename))
        zf.writestr(name, data)
    return target.getvalue()


def test_canonical_manifest_signature_ignores_mapping_order(ed25519_keys: tuple[bytes, bytes]):
    private_key, public_key = ed25519_keys
    first = {"files": {"b": 2, "a": 1}, "app_version": "1.2.3"}
    reordered = {"app_version": "1.2.3", "files": {"a": 1, "b": 2}}

    assert canonical_manifest_bytes(first) == canonical_manifest_bytes(reordered)
    verify_manifest_signature(reordered, sign_manifest(first, private_key), public_key)


def test_signed_release_roundtrip(tmp_path: Path, ed25519_keys: tuple[bytes, bytes]):
    private_key, public_key = ed25519_keys
    archive = build_archive(
        kind=KIND_RELEASE,
        source_root=release_tree(tmp_path),
        app_version="1.2.3",
        release_meta={"git_sha": "a" * 40, "migration_head": "0017_driver_work_reports"},
        signing_key=private_key,
    )

    meta = inspect_archive(archive, expected_kind=KIND_RELEASE, public_key=public_key)

    assert meta.format == 2
    assert meta.app_version == "1.2.3"
    assert meta.git_sha == "a" * 40


def test_tampered_signed_release_is_rejected(tmp_path: Path, ed25519_keys: tuple[bytes, bytes]):
    archive = tamper_manifest(build_signed_release(tmp_path, ed25519_keys), "app_version", "9.9.9")

    with pytest.raises(ArchiveError, match="signature_invalid"):
        inspect_archive(archive, expected_kind=KIND_RELEASE, public_key=ed25519_keys[1])


def test_release_rejects_dot_path_alias_before_extraction(
    tmp_path: Path, ed25519_keys: tuple[bytes, bytes]
):
    archive = append_member(
        build_signed_release(tmp_path, ed25519_keys), "apps/api/./main.py", b"ATTACKER"
    )

    with pytest.raises(ArchiveError, match="unsafe_path"):
        inspect_archive(archive, expected_kind=KIND_RELEASE, public_key=ed25519_keys[1])
    with pytest.raises(ArchiveError, match="unsafe_path"):
        unpack_archive(
            archive,
            tmp_path / "out",
            expected_kind=KIND_RELEASE,
            public_key=ed25519_keys[1],
        )
    assert not (tmp_path / "out" / "apps" / "api" / "main.py").exists()


@pytest.mark.parametrize(
    "alias",
    [
        "apps\\api\\main.py",
        "apps/api/../main.py",
        "apps//api/main.py",
        " apps/api/main.py",
        "apps/api/main.py ",
    ],
)
def test_release_rejects_noncanonical_member_aliases(
    tmp_path: Path, ed25519_keys: tuple[bytes, bytes], alias: str
):
    archive = append_member(build_signed_release(tmp_path, ed25519_keys), alias, b"ATTACKER")

    with pytest.raises(ArchiveError, match="unsafe_path"):
        inspect_archive(archive, expected_kind=KIND_RELEASE, public_key=ed25519_keys[1])


def test_signing_helpers_reject_ed448_keys():
    private = Ed448PrivateKey.generate()
    private_key = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_key = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    manifest = {"app_version": "1.2.3"}

    with pytest.raises(ArchiveError, match="signature_invalid"):
        sign_manifest(manifest, private_key)
    signature = private.sign(canonical_manifest_bytes(manifest))
    with pytest.raises(ArchiveError, match="signature_invalid"):
        verify_manifest_signature(manifest, signature, public_key)


def test_release_requires_a_trusted_public_key(tmp_path: Path, ed25519_keys: tuple[bytes, bytes]):
    archive = build_signed_release(tmp_path, ed25519_keys)

    with pytest.raises(ArchiveError, match="signature_invalid"):
        inspect_archive(archive, expected_kind=KIND_RELEASE)


def test_release_rejects_duplicate_and_symbolic_link_members(
    tmp_path: Path, ed25519_keys: tuple[bytes, bytes]
):
    archive = build_signed_release(tmp_path, ed25519_keys)
    source = zipfile.ZipFile(io.BytesIO(archive))
    duplicate = io.BytesIO()
    with source, zipfile.ZipFile(duplicate, "w") as zf:
        for info in source.infolist():
            zf.writestr(info.filename, source.read(info.filename))
        with pytest.warns(UserWarning, match="Duplicate name"):
            zf.writestr("apps/api/main.py", b"duplicate")
    with pytest.raises(ArchiveError, match="duplicate_member"):
        inspect_archive(duplicate.getvalue(), expected_kind=KIND_RELEASE, public_key=ed25519_keys[1])

    symlink = io.BytesIO()
    with zipfile.ZipFile(symlink, "w") as zf:
        for info in zipfile.ZipFile(io.BytesIO(archive)).infolist():
            zf.writestr(info.filename, zipfile.ZipFile(io.BytesIO(archive)).read(info.filename))
        link = zipfile.ZipInfo("apps/api/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, b"main.py")
    with pytest.raises(ArchiveError, match="unsafe_path"):
        inspect_archive(symlink.getvalue(), expected_kind=KIND_RELEASE, public_key=ed25519_keys[1])


def test_key_generator_writes_a_protected_private_key(tmp_path: Path):
    private = tmp_path / "private.pem"
    public = tmp_path / "public.pem"
    script = Path(__file__).parents[3] / "scripts" / "generate-release-key.py"

    generated = subprocess.run(
        [sys.executable, script, "--private", private, "--public", public],
        capture_output=True,
        text=True,
        check=False,
    )

    assert generated.returncode == 0, generated.stderr
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    assert public.is_file()
    duplicate = subprocess.run(
        [sys.executable, script, "--private", private, "--public", public],
        capture_output=True,
        text=True,
        check=False,
    )
    assert duplicate.returncode != 0


def test_release_pack_creates_a_signed_archive(tmp_path: Path, ed25519_keys: tuple[bytes, bytes]):
    private, public = ed25519_keys
    key_path = tmp_path / "release-key.pem"
    key_path.write_bytes(private)
    key_path.chmod(0o600)
    output = tmp_path / "release.zip"
    script = Path(__file__).parents[3] / "scripts" / "release_pack.py"

    packed = subprocess.run(
        [
            "/usr/bin/python3",
            script,
            "--root",
            release_tree(tmp_path),
            "--output",
            output,
            "--version",
            "1.2.3",
            "--git-sha",
            "a" * 40,
            "--migration-head",
            "0017_driver_work_reports",
            "--signing-key",
            key_path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert packed.returncode == 0, packed.stderr
    assert inspect_archive(output.read_bytes(), expected_kind=KIND_RELEASE, public_key=public).git_sha == "a" * 40


def test_release_pack_refuses_to_invent_a_base_migration_head(
    tmp_path: Path, ed25519_keys: tuple[bytes, bytes]
):
    private, _public = ed25519_keys
    key_path = tmp_path / "release-key.pem"
    key_path.write_bytes(private)
    key_path.chmod(0o600)
    script = Path(__file__).parents[3] / "scripts" / "release_pack.py"

    packed = subprocess.run(
        [
            "/usr/bin/python3",
            script,
            "--root",
            release_tree(tmp_path),
            "--output",
            tmp_path / "release.zip",
            "--version",
            "1.2.3",
            "--git-sha",
            "a" * 40,
            "--signing-key",
            key_path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert packed.returncode != 0
    assert "migration" in packed.stderr


def test_pack_release_wrapper_propagates_the_explicit_migration_head(
    tmp_path: Path, ed25519_keys: tuple[bytes, bytes]
):
    private, public = ed25519_keys
    key_path = tmp_path / "release-key.pem"
    key_path.write_bytes(private)
    key_path.chmod(0o600)
    root = Path(__file__).parents[3]
    output = tmp_path / "release.zip"
    env = {
        **os.environ,
        "ROBOPARK_SIGNING_KEY_FILE": str(key_path),
        "ROBOPARK_MIGRATION_HEAD": "0017_driver_work_reports",
        "ROBOPARK_RELEASE_VERSION": "1.2.3",
    }

    packed = subprocess.run(
        [root / "scripts" / "pack-release.sh", output],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert packed.returncode == 0, packed.stderr
    meta = inspect_archive(output.read_bytes(), expected_kind=KIND_RELEASE, public_key=public)
    assert meta.migration_head == "0017_driver_work_reports"
