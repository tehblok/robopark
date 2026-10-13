from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ROOT = Path(__file__).resolve().parents[2]


def builder():
    spec = importlib.util.spec_from_file_location(
        "build_macos_pkg", ROOT / "scripts/build_macos_pkg.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_catalog_is_signed_binds_exact_bytes_and_uses_persistent_root(tmp_path):
    module = builder()
    package = tmp_path / "Robopark-0.2.0-rc.44-arm64.pkg"
    package.write_bytes(b"test-only-pkg-bytes")
    private = tmp_path / "private.key"
    public = module.ensure_update_key(private)
    assert module.ensure_update_key(private) == public
    assert private.stat().st_mode & 0o777 == 0o600
    envelope = module.sign_catalog(
        package, "0.2.0-rc.44", private, generation=11, now=1000
    )
    payload = base64.b64decode(envelope["payload"])
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public)).verify(
        base64.b64decode(envelope["signature"]), payload
    )
    catalog = json.loads(payload)
    assert catalog["generation"] == 11
    assert catalog["expires"] == 1000 + 14 * 86400
    assert catalog["channels"]["rc"]["length"] == len(package.read_bytes())
    assert (
        catalog["channels"]["rc"]["url"] == "/api/distribution/v1/macos/" + package.name
    )
    assert set(catalog["channels"]) == {"rc"}


def test_installer_runs_as_user_and_does_not_embed_private_key(tmp_path):
    module = builder()
    import plistlib

    profile = module.launch_agent()
    assert profile["RunAtLoad"] is True
    assert "UserName" not in profile
    assert profile["KeepAlive"] == {"SuccessfulExit": False}
    assert profile["ProgramArguments"][-1] == "--background"
    assert "private" not in plistlib.dumps(profile).decode()


def test_invalid_versions_are_not_used_as_package_paths():
    import pytest

    module = builder()
    for version in ("../../bad", "1;echo bad", ""):
        with pytest.raises(ValueError):
            module.package_name(version)


def test_bundle_versions_are_apple_numeric_and_keep_release_identity():
    import re
    module = builder()
    first = module.bundle_versions("0.2.0-rc.44.dev7114536505405942333", 1791900000)
    later = module.bundle_versions("0.2.0-rc.44.dev7114536505405942334", 1791986400)
    assert first["CFBundleShortVersionString"] == "0.2.0"
    assert first["RoboparkReleaseVersion"].endswith("2333")
    assert re.fullmatch(r"[1-9][0-9]{0,3}\.[0-9]{1,2}\.[0-9]{1,2}", first["CFBundleVersion"])
    assert tuple(map(int, later["CFBundleVersion"].split("."))) > tuple(map(int, first["CFBundleVersion"].split(".")))


def test_deployment_signature_binds_environment_instance_and_origin(tmp_path):
    import time

    from scripts.sign_macos_deployment import sign_profile
    module = builder()
    private = tmp_path / "private.key"
    public = module.ensure_update_key(private)
    envelope = sign_profile(origin="https://TEST.example:443/", instance_id="50b4ae12-c0d8-4c76-9639-2984f164408b",
                            environment="test", channel="rc", expires=int(time.time()) + 86400, key=private)
    data = base64.b64decode(envelope["payload"])
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public)).verify(base64.b64decode(envelope["signature"]), data)
    profile = json.loads(data)
    assert profile["origin"] == "https://test.example"
    assert profile["environment"] == "test"
    assert profile["purpose"] == "robopark-deployment-v1"


def test_workspace_bundle_contains_verified_assets_and_source_identity(tmp_path):
    import hashlib

    module = builder()
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<head></head><script src="/assets/app.js"></script>')
    (dist / "assets/app.js").write_text('console.log("offline")')
    target = tmp_path / "workspace"
    result = module.stage_workspace(dist, target, version="0.2.0-rc.44", commit="a" * 40)
    assert result["version"] == "0.2.0-rc.44"
    assert result["source_commit"] == "a" * 40
    assert (target / "index.html").read_bytes() == (dist / "index.html").read_bytes()
    assert result["files"]["assets/app.js"]["sha256"] == hashlib.sha256((target / "assets/app.js").read_bytes()).hexdigest()
    assert json.loads((target / "workspace-manifest.json").read_text()) == result


def test_workspace_bundle_rejects_missing_assets_and_symlink(tmp_path):
    import pytest

    module = builder()
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text('<script src="/assets/missing.js"></script>')
    with pytest.raises(ValueError, match="workspace_entry_asset_missing"):
        module.stage_workspace(dist, tmp_path / "first", version="1.0.0", commit="a" * 40)
    (dist / "index.html").write_text('<head></head>')
    (dist / "outside.js").symlink_to(tmp_path / "private.key")
    with pytest.raises(ValueError, match="workspace_symlink"):
        module.stage_workspace(dist, tmp_path / "second", version="1.0.0", commit="a" * 40)
