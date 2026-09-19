"""Signed bridge releases rotate admission authority, never arbitrary old-key releases."""

import json
import os
import zipfile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from robopark_host.updater import apply_release, reconcile_after_exit, recover_interrupted_update
from test_updater import host as host_factory


@pytest.fixture
def host(host_paths):
    return host_factory.__wrapped__(host_paths)


def new_key():
    key = Ed25519PrivateKey.generate()
    return (
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ),
    )


def bridge(host):
    private, public = new_key()
    artifact = host.package(
        meta={
            "signing_key_rotation": {
                "next_public_key": public.decode(),
                "activation_version": "2.0.0",
            }
        }
    )
    return host.request(artifact), private, public


def finish(host, request):
    assert apply_release(request, host.paths, host.runner).error is None
    return reconcile_after_exit(host.paths, host.runner)


def test_rotation_does_not_block_local_release_signed_with_old_key(host):
    request, _, public = bridge(host)
    assert finish(host, request).state == "current_healthy"
    assert (host.paths.etc / "release-public-key.pem").read_bytes() == public
    old_future = host.package("3.0.0")
    assert (
        apply_release(host.request(old_future), host.paths, host.runner).state
        == "awaiting_reconciliation"
    )


def test_pre_health_failure_never_promotes_key(host):
    original = (host.paths.etc / "release-public-key.pem").read_bytes()
    request, _, _ = bridge(host)
    host.runner.health = False
    apply_release(request, host.paths, host.runner)
    assert (host.paths.etc / "release-public-key.pem").read_bytes() == original
    assert host.paths.current.resolve().name == "1.0.0"


def test_api_accepts_authenticated_projection_while_old_pem_is_bind_mounted(host):
    from robopark_api.services.ops import release_signing

    original = (host.paths.etc / "release-public-key.pem").read_bytes()
    request, _, public = bridge(host)
    finish(host, request)
    projection = json.loads((host.paths.ops / "public/signing-trust.json").read_text())
    assert hasattr(release_signing, "projected_admission_key")
    assert release_signing.projected_admission_key(original, projection) == public
    projection["certificate"]["manifest"]["app_version"] = "99.0.0"
    with pytest.raises(ValueError):
        release_signing.projected_admission_key(original, projection)


def test_installer_bridge_requires_readiness_and_can_resume_projection(host):
    from robopark_host import trust

    original = (host.paths.etc / "release-public-key.pem").read_bytes()
    _, public = new_key()
    artifact = host.package(
        "1.0.0",
        meta={
            "migration_head": "old",
            "signing_key_rotation": {
                "next_public_key": public.decode(),
                "activation_version": "1.0.0",
            },
        },
    )
    with zipfile.ZipFile(artifact) as archive:
        archive.extractall(host.paths.current.resolve())
    assert hasattr(trust, "bootstrap")
    (host.paths.state / "maintenance.json").write_text("{}")  # fake readiness adapter
    host.runner.previous_health = False
    with pytest.raises(ValueError):
        trust.bootstrap(host.paths, host.runner)
    assert (host.paths.etc / "release-public-key.pem").read_bytes() == original
    host.runner.previous_health = True
    (host.paths.state / "maintenance.json").write_text("{}")  # fake runner readiness assertion
    trust.bootstrap(host.paths, host.runner)
    trust.bootstrap(host.paths, host.runner)
    assert (host.paths.etc / "release-public-key.pem").read_bytes() == public


@pytest.mark.parametrize("failure_point", ["signing-trust.json", "release-public-key.pem"])
def test_activation_crash_finishes_with_new_authority_on_healthy_replay(
    host, monkeypatch, failure_point
):
    request, _, public = bridge(host)
    apply_release(request, host.paths, host.runner)
    native = os.replace

    class PowerLoss(BaseException):
        pass

    def replace(src, dst):
        native(src, dst)
        if str(dst).endswith(failure_point):
            raise PowerLoss()

    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(PowerLoss):
        reconcile_after_exit(host.paths, host.runner)
    monkeypatch.setattr(os, "replace", native)
    assert recover_interrupted_update(host.paths, host.runner).state == "current_healthy"
    assert (host.paths.etc / "release-public-key.pem").read_bytes() == public


@pytest.mark.parametrize("fail_projection", ["release-public-key.pem", "signing-trust.json"])
def test_activation_crash_replays_durable_trust_and_rollback(host, monkeypatch, fail_projection):
    original = (host.paths.etc / "release-public-key.pem").read_bytes()
    request, _, public = bridge(host)
    apply_release(request, host.paths, host.runner)
    native_replace = os.replace

    class PowerLoss(BaseException):
        pass

    tripped = False

    def replace(src, dst):
        nonlocal tripped
        native_replace(src, dst)
        if not tripped and str(dst).endswith(fail_projection):
            tripped = True
            raise PowerLoss()

    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(PowerLoss):
        reconcile_after_exit(host.paths, host.runner)
    monkeypatch.setattr(os, "replace", native_replace)
    assert tripped
    host.runner.health = False
    result = recover_interrupted_update(host.paths, host.runner)
    assert result.state != "maintenance"
    assert host.paths.current.resolve().name == "1.0.0"
    assert (host.paths.etc / "release-public-key.pem").read_bytes() == original
    assert public != original


def test_completed_bridge_request_replay_does_not_require_old_admission(host):
    request, _, _ = bridge(host)
    finish(host, request)
    before = len(host.runner.commands)
    assert apply_release(request, host.paths, host.runner).state == "current_healthy"
    assert len(host.runner.commands) == before


def test_signature_source_does_not_block_local_bridge(host):
    _, private, _ = bridge(host)
    _, public = new_key()
    host.private = private
    forged = host.package(
        meta={
            "signing_key_rotation": {
                "next_public_key": public.decode(),
                "activation_version": "2.0.0",
            }
        }
    )
    assert (
        apply_release(host.request(forged), host.paths, host.runner).state
        == "awaiting_reconciliation"
    )
    assert not (host.paths.state / "signing-trust.json").exists()


def test_next_key_update_failure_retains_rotated_key_and_old_signed_bridge(host):
    from robopark_host.release import verify_directory
    from robopark_host.trust import admission_key, directory_key

    request, private, public = bridge(host)
    finish(host, request)
    current = host.paths.current.resolve()
    host.private = private
    successor = host.request(host.package("3.0.0"))
    host.runner.health = False
    apply_release(successor, host.paths, host.runner)
    assert host.paths.current.resolve() == current
    assert admission_key(host.paths) == public
    assert verify_directory(current, directory_key(host.paths, current))["app_version"] == "2.0.0"


@pytest.mark.parametrize(
    "field,value",
    [("activation_version", "3.0.0"), ("next_public_key", "not a key"), ("unknown", "unexpected")],
)
def test_rotation_exact_schema_is_rejected_by_all_verifiers(host, field, value):
    import importlib.util
    import io
    from pathlib import Path

    from robopark_api.services.ops.archives import inspect_archive
    from robopark_api.services.ops.release_signing import sign_manifest
    from robopark_host.release import verify_archive

    request, _, _ = bridge(host)
    with zipfile.ZipFile(host.paths.ops / "artifacts" / request.artifact) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(files["manifest.json"])
    manifest["signing_key_rotation"][field] = value
    files["manifest.json"] = json.dumps(manifest).encode()
    files["manifest.sig"] = sign_manifest(manifest, host.private)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    public = (host.paths.etc / "release-public-key.pem").read_bytes()
    verifier = Path(__file__).resolve().parents[2] / "scripts/verify-artifact.py"
    spec = importlib.util.spec_from_file_location("rotation_offline_verifier", verifier)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for verify in (
        lambda: inspect_archive(buffer.getvalue(), expected_kind="release", public_key=public),
        lambda: verify_archive(buffer.getvalue(), public),
        lambda: module.verify_release(buffer.getvalue(), module.public_key(public)),
    ):
        with pytest.raises(ValueError):
            verify()


def test_same_key_rotation_is_rejected_even_with_noncanonical_anchor_encoding(host):
    from robopark_host.release import verify_archive

    anchor = (host.paths.etc / "release-public-key.pem").read_bytes()
    artifact = host.package(
        meta={
            "signing_key_rotation": {
                "next_public_key": anchor.decode(),
                "activation_version": "2.0.0",
            }
        }
    )
    with pytest.raises(ValueError):
        verify_archive(artifact.read_bytes(), anchor.replace(b"\n", b"\r\n"))


def test_bridge_activation_between_preflight_and_lock_keeps_local_candidate(host, monkeypatch):
    from contextlib import contextmanager

    from robopark_host import updater

    request, _, public = bridge(host)
    assert apply_release(request, host.paths, host.runner).state == "awaiting_reconciliation"
    old_future = host.request(host.package("3.0.0"))
    original = updater.exclusive_lock

    @contextmanager
    def interleave(path, *args, **kwargs):
        monkeypatch.setattr(updater, "exclusive_lock", original)
        assert reconcile_after_exit(host.paths, host.runner).state == "current_healthy"
        assert (host.paths.etc / "release-public-key.pem").read_bytes() == public
        with original(path, *args, **kwargs):
            yield

    monkeypatch.setattr(updater, "exclusive_lock", interleave)
    result = apply_release(old_future, host.paths, host.runner)
    assert result.state == "awaiting_reconciliation"


def test_manual_restore_verifies_bridge_through_exact_retained_pin(host, monkeypatch):
    import hashlib
    from uuid import uuid4

    from robopark_api.services.ops.archives import build_archive
    from robopark_host.restore import run_restore
    from test_manual_restore import database

    request, _, public = bridge(host)
    assert finish(host, request).state == "current_healthy"
    host_env = host.paths.etc / "host.env"
    with host_env.open("a") as stream:
        stream.write("ROBOPARK_DATABASE_PROFILE=sqlite-offline-legacy\n")
    source = host.paths.root / "manual-snapshot"
    database(source / "data/robopark.db", "restored", "new")
    blob = build_archive(kind="snapshot", source_root=source, app_version="2.0.0")
    identity = str(uuid4())
    artifact = "restore-" + identity + ".zip"
    (host.paths.ops / "artifacts" / artifact).write_bytes(blob)
    request = {
        "job_id": identity,
        "kind": "restore",
        "actor_user_id": 7,
        "created_at": "2026-09-07T00:00:00+00:00",
        "artifact": artifact,
        "sha256": hashlib.sha256(blob).hexdigest(),
    }
    result = run_restore(host.paths, request, host.runner)
    assert result["state"] == "succeeded", result
    assert (host.paths.etc / "release-public-key.pem").read_bytes() == public


def test_api_old_inspection_survives_key_projection_change(host):
    from types import SimpleNamespace

    from robopark_api.services.ops import host_bridge

    # Simulate the API's old bind-mounted PEM while root atomically replaces it.
    anchor = host.paths.root / "api-anchor.pem"
    anchor.write_bytes((host.paths.etc / "release-public-key.pem").read_bytes())
    settings = SimpleNamespace(
        ops_release_public_key_path=str(anchor), ops_max_upload_bytes=512 * 1024**2
    )
    ops = host.paths.var / "api-ops"
    old_future = host.package("3.0.0").read_bytes()
    inspected = host_bridge.inspect_update(settings, ops, host.paths.ops, old_future, 7)
    request, _, _ = bridge(host)
    finish(host, request)
    (host.paths.ops / "inbox").mkdir(exist_ok=True)
    job = host_bridge.approve_update(
        settings, ops, host.paths.ops, inspected.inspection_id, 7, None
    )
    assert job.kind == "update" and job.phase == "awaiting_host"
