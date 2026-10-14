from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
import zipfile
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from scripts import build_khadas_test_setup as builder


def assets(tmp_path, monkeypatch, *, assignment_extra=None, envelope_extra=None):
    ota = tmp_path / "robopark-0.2.3-rc.1.ota"
    ota.write_bytes(b"unchanged OTA")
    private = Ed25519PrivateKey.generate()
    root = {
        "ed25519": base64.b64encode(
            private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        ).decode()
    }
    monkeypatch.setattr(builder, "EXPECTED_UPDATE_ROOT", root["ed25519"])
    instance = "6f19f28b-0d04-4bba-ad8b-327f113dc855"
    payload = json.dumps(
        {
            "purpose": "robopark-deployment-v1",
            "origin": builder.ORIGIN,
            "instance_id": instance,
            "environment": "test",
            "channel": "rc",
            "expires": 4102444800,
            **(assignment_extra or {}),
        }
    ).encode()
    envelope = {
        "payload": base64.b64encode(payload).decode(),
        "signature": base64.b64encode(private.sign(payload)).decode(),
        **(envelope_extra or {}),
    }
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps(envelope))
    public = tmp_path / "update-root.json"
    public.write_text(json.dumps(root))
    return ota, profile, public, instance


@pytest.mark.parametrize("extra", ["assignment", "envelope"])
def test_builder_rejects_signed_profile_schema_runtime_would_reject(
    tmp_path, monkeypatch, extra
):
    ota, profile, public, instance = assets(
        tmp_path, monkeypatch, **{f"{extra}_extra": {"note": "unsupported"}}
    )
    output = tmp_path / "install.pyz"
    with pytest.raises(ValueError, match="profile_schema_invalid"):
        builder.build_bundle(output, ota, profile, public, instance, verify=fake_ota, version="0.2.3-rc.1")
    assert not output.exists()


def fake_ota(path):
    return SimpleNamespace(
        manifest=SimpleNamespace(app_version="0.2.3-rc.1"),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        size=path.stat().st_size,
    )


def test_bundle_preserves_ota_and_only_whitelisted_public_files(tmp_path, monkeypatch):
    ota, profile, public, instance = assets(tmp_path, monkeypatch)
    output = tmp_path / "install.pyz"
    builder.build_bundle(
        output, ota, profile, public, instance, verify=fake_ota, version="0.2.3-rc.1"
    )
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {
            "__main__.py",
            "khadas_test_setup.py",
            "khadas_test_isolation.py",
            "target.json",
            "profile.json",
            "update-root.json",
            ota.name,
        }
        assert archive.read(ota.name) == ota.read_bytes()
        target = json.loads(archive.read("target.json"))
        assert target["instance_id"] == instance
        assert target["origin"] == "https://roboparktest.ru.tuna.am"
        assert target["ota_sha256"] == hashlib.sha256(ota.read_bytes()).hexdigest()
        assert (
            target["profile_sha256"] == hashlib.sha256(profile.read_bytes()).hexdigest()
        )
    first = output.read_bytes()
    output.unlink()
    builder.build_bundle(
        output, ota, profile, public, instance, verify=fake_ota, version="0.2.3-rc.1"
    )
    assert output.read_bytes() == first


def test_other_instance_or_untrusted_signature_prevents_output(tmp_path, monkeypatch):
    ota, profile, public, instance = assets(tmp_path, monkeypatch)
    output = tmp_path / "install.pyz"
    with pytest.raises(ValueError, match="profile_assignment_mismatch"):
        builder.build_bundle(
            output,
            ota,
            profile,
            public,
            "e974bc2a-82d2-46af-be60-21c6665072aa",
            verify=fake_ota,
            version="0.2.3-rc.1",
        )
    assert not output.exists()
    envelope = json.loads(profile.read_text())
    envelope["signature"] = base64.b64encode(b"0" * 64).decode()
    profile.write_text(json.dumps(envelope))
    with pytest.raises(ValueError, match="profile_signature_invalid"):
        builder.build_bundle(
            output,
            ota,
            profile,
            public,
            instance,
            verify=fake_ota,
            version="0.2.3-rc.1",
        )
    assert not output.exists()


def test_existing_bundle_never_overwritten(tmp_path, monkeypatch):
    ota, profile, public, instance = assets(tmp_path, monkeypatch)
    output = tmp_path / "install.pyz"
    output.write_bytes(b"previous verified delivery")
    with pytest.raises(FileExistsError):
        builder.build_bundle(
            output,
            ota,
            profile,
            public,
            instance,
            verify=fake_ota,
            version="0.2.3-rc.1",
        )
    assert output.read_bytes() == b"previous verified delivery"


def test_signed_but_unreviewed_root_prevents_delivery(tmp_path, monkeypatch):
    ota, profile, public, instance = assets(tmp_path, monkeypatch)
    monkeypatch.setattr(builder, "EXPECTED_UPDATE_ROOT", "different-reviewed-key")
    with pytest.raises(ValueError, match="untrusted_update_root"):
        builder.build_bundle(
            tmp_path / "install.pyz", ota, profile, public, instance, verify=fake_ota, version="0.2.3-rc.1"
        )


@pytest.mark.parametrize("mismatch", ["name", "version", "contents"])
def test_build_rejects_ota_that_runtime_would_refuse(tmp_path, monkeypatch, mismatch):
    ota, profile, public, instance = assets(tmp_path, monkeypatch)
    verify = fake_ota
    if mismatch == "name":
        ota = ota.rename(tmp_path / "renamed.ota")
    elif mismatch == "version":

        def verify(_):
            return SimpleNamespace(manifest=SimpleNamespace(app_version="0.2.0-rc.43"))
    else:

        def verify(path):
            verified = fake_ota(path)
            path.write_bytes(b"different OTA")
            return verified

    output = tmp_path / "install.pyz"
    with pytest.raises(
        ValueError, match="target_ota_invalid|ota_changed_after_verification"
    ):
        builder.build_bundle(
            output, ota, profile, public, instance, verify=verify, version="0.2.3-rc.1"
        )
    assert not output.exists()


def test_generated_installer_verifies_new_release_and_rejects_changed_target(
    tmp_path, monkeypatch
):
    ota, profile, public, instance = assets(tmp_path, monkeypatch)
    output = tmp_path / "install.pyz"
    builder.build_bundle(
        output, ota, profile, public, instance, verify=fake_ota, version="0.2.3-rc.1"
    )
    result = subprocess.run(
        [sys.executable, str(output), "--verify"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"version": "0.2.3-rc.1", "state": "verified"}
    with zipfile.ZipFile(output) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    changed = b"different OTA"
    target = json.loads(members["target.json"])
    target.update(ota_sha256=hashlib.sha256(changed).hexdigest(), ota_size=len(changed))
    members["target.json"] = json.dumps(target).encode()
    members[ota.name] = changed
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    result = subprocess.run(
        [sys.executable, str(output), "--verify"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "target_ota_invalid" in result.stderr
