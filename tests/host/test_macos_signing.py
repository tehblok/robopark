from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS code signing")


def signing_module():
    spec = importlib.util.spec_from_file_location(
        "macos_signing", ROOT / "scripts/macos_signing.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compile_fixture(tmp_path: Path, name: str) -> Path:
    source = tmp_path / f"{name}.swift"
    source.write_text(
        """
import Foundation
import Security
import LocalAuthentication

guard CommandLine.arguments.count == 2 else { exit(64) }
var keychain: SecKeychain?
guard SecKeychainOpen(CommandLine.arguments[1], &keychain) == errSecSuccess,
      let keychain else { exit(65) }
let query: [String: Any] = [
    kSecClass as String: kSecClassGenericPassword,
    kSecAttrService as String: "ru.robopark.signing-fixture",
    kSecAttrAccount as String: "fixture",
    kSecMatchSearchList as String: [keychain],
    kSecReturnData as String: true,
    kSecUseAuthenticationContext as String: {
        let context = LAContext()
        context.interactionNotAllowed = true
        return context
    }(),
]
var result: CFTypeRef?
guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
      let value = result as? Data,
      value == Data("fixture-only-value".utf8) else { exit(66) }
"""
    )
    binary = tmp_path / name
    subprocess.run(["swiftc", str(source), "-o", str(binary)], check=True)
    return binary


def test_test_identity_is_persistent_private_and_reused(tmp_path):
    module = signing_module()
    secret_dir = tmp_path / ".release-secrets"
    first = module.ensure_test_signing_identity(secret_dir, initialize=True)
    second = module.ensure_test_signing_identity(secret_dir)

    assert first.fingerprint == second.fingerprint
    assert first.private_key == second.private_key
    assert first.private_key.stat().st_mode & 0o777 == 0o600
    assert secret_dir.stat().st_mode & 0o777 == 0o700
    assert first.private_key.read_bytes() not in first.certificate.read_bytes()


def test_two_builds_share_designated_requirement_and_wrong_certificate_is_rejected(tmp_path):
    module = signing_module()
    first_identity = module.ensure_test_signing_identity(tmp_path / "stable", initialize=True)
    wrong_identity = module.ensure_test_signing_identity(tmp_path / "wrong", initialize=True)
    first = compile_fixture(tmp_path, "fixture-first")
    second = compile_fixture(tmp_path, "fixture-second")
    wrong = compile_fixture(tmp_path, "fixture-wrong")

    with module.isolated_test_signer(first_identity) as signer:
        signer.sign(first, identifier="ru.robopark.signing-fixture")
    with module.isolated_test_signer(first_identity) as signer:
        signer.sign(second, identifier="ru.robopark.signing-fixture")
    with module.isolated_test_signer(wrong_identity) as signer:
        signer.sign(wrong, identifier="ru.robopark.signing-fixture")

    first_requirement = module.designated_requirement(first)
    assert module.designated_requirement(second) == first_requirement
    assert module.designated_requirement(wrong) != first_requirement
    assert module.satisfies_designated_requirement(first, first_requirement)
    assert module.satisfies_designated_requirement(second, first_requirement)
    assert not module.satisfies_designated_requirement(wrong, first_requirement)

    runtime_keychain = tmp_path / "runtime.keychain-db"
    module.create_fixture_keychain(runtime_keychain, trusted_application=first)
    try:
        assert subprocess.run([str(first), str(runtime_keychain)], check=False, timeout=5).returncode == 0
        assert subprocess.run([str(second), str(runtime_keychain)], check=False, timeout=5).returncode == 0
    finally:
        module.delete_fixture_keychain(runtime_keychain)


def test_isolated_signer_restores_user_keychain_search_list(tmp_path):
    module = signing_module()
    identity = module.ensure_test_signing_identity(tmp_path / "stable", initialize=True)
    before = subprocess.run(
        ["security", "list-keychains", "-d", "user"], capture_output=True, check=True
    ).stdout
    with module.isolated_test_signer(identity) as signer:
        during = subprocess.run(
            ["security", "list-keychains", "-d", "user"], capture_output=True, check=True
        ).stdout
        assert during != before
        assert str(signer.keychain).encode() in during
    after = subprocess.run(
        ["security", "list-keychains", "-d", "user"], capture_output=True, check=True
    ).stdout
    assert after == before
    assert not any(path.suffix == ".keychain-db" for path in tmp_path.rglob("*"))


def test_search_list_cleanup_preserves_concurrent_changes():
    module = signing_module()
    original = ["/Users/test/Library/Keychains/login.keychain-db"]
    owned = "/private/tmp/robopark-signing/signing.keychain-db"
    assert module.restored_search_list(
        original=original, current=[*original, owned], owned=owned
    ) == original
    assert module.restored_search_list(
        original=original,
        current=[*original, owned, "/Users/test/Library/Keychains/concurrent.keychain-db"],
        owned=owned,
    ) == [*original, "/Users/test/Library/Keychains/concurrent.keychain-db"]



def test_release_signing_fails_closed_without_identity_or_with_wrong_pin(tmp_path):
    module = signing_module()
    with pytest.raises(ValueError, match="missing_restore_from_backup"):
        module.ensure_test_signing_identity(tmp_path / "missing")
    identity = module.ensure_test_signing_identity(tmp_path / "stable", initialize=True)
    with pytest.raises(ValueError, match="differs_from_release_pin"):
        module.ensure_test_signing_identity(tmp_path / "stable", expected_fingerprint="0" * 64)
    assert module.ensure_test_signing_identity(tmp_path / "stable", expected_fingerprint=identity.fingerprint) == identity
