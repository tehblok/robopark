"""The real installer resumes an exact installed bridge without re-admitting its key."""

import json

import pytest
from cryptography.hazmat.primitives import serialization
from robopark_api.services.ops.archives import KIND_RELEASE, build_archive
from test_key_rotation import new_key


@pytest.fixture
def installer():
    from installer_scenarios import InstallerScenarios

    scenario = InstallerScenarios()
    scenario.setUp()
    # Keep real helper/trust/fsync behavior; adapt only external commands to the
    # disposable installer's fake systemd/Docker environment, as the E2E runner does.
    helper = scenario.bundle / "lib/install-trust.py"
    source = helper.read_text()
    environment = {key: scenario.env[key] for key in ("PATH", "ROBOPARK_ROOT", "ROBOPARK_TESTING")}
    adapter = f"""
    class FixtureRunner(SystemRunner):
        def run(self, argv, **kwargs):
            kwargs["env"] = {{**{environment!r}, **(kwargs.get("env") or {{}})}}
            return super().run(argv, **kwargs)
"""
    source = source.replace(
        "    bootstrap(HostPaths.from_root(root), SystemRunner())",
        adapter + "\n    bootstrap(HostPaths.from_root(root), FixtureRunner())",
    )
    helper.write_text(source)
    try:
        yield scenario
    finally:
        scenario.doCleanups()


def bridge_bundle(installer):
    _, public = new_key()
    installer.payload.write_bytes(
        build_archive(
            kind=KIND_RELEASE,
            source_root=installer.source,
            app_version="1.0.0",
            release_meta={
                "git_sha": "a" * 40,
                "migration_head": "initial",
                "signing_key_rotation": {
                    "next_public_key": public.decode(),
                    "activation_version": "1.0.0",
                },
            },
            signing_key=installer.key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )
    )
    return public


def test_same_installer_resume_and_rerun_keep_rotated_authority(installer):
    public = bridge_bundle(installer)
    installer.run_installer()
    key = installer.root / "etc/robopark/release-public-key.pem"
    state = installer.root / "var/lib/robopark/ops/state/signing-trust.json"
    before = state.read_bytes()
    assert key.read_bytes() == public
    installer.run_installer("--resume")
    installer.run_installer()
    assert key.read_bytes() == public
    assert state.read_bytes() == before
    assert installer.state()["phase"] == "complete"


@pytest.mark.parametrize(
    "crash_at", ["signing-trust.json", "release-public-key.pem", "after-trust"]
)
def test_resume_completes_trust_projection_after_activation_crash(installer, crash_at):
    public = bridge_bundle(installer)
    helper = installer.bundle / "lib/install-trust.py"
    original = helper.read_text()
    if crash_at == "after-trust":
        injected = original.replace("        main()", "        main()\n        os._exit(77)")
    else:
        target = installer.root / (
            "var/lib/robopark/ops/state/signing-trust.json"
            if crash_at == "signing-trust.json"
            else "etc/robopark/release-public-key.pem"
        )
        hook = f"""\n_native_replace = os.replace\ndef crash_replace(source, destination):\n    _native_replace(source, destination)\n    if str(destination) == {str(target)!r}:\n        os._exit(77)\nos.replace = crash_replace\n"""
        injected = original.replace(
            'if __name__ == "__main__":', hook + '\nif __name__ == "__main__":'
        )
    helper.write_text("import os\n" + injected)
    installer.run_installer(success=False)
    assert installer.state()["phase"] == "services"
    state = installer.root / "var/lib/robopark/ops/state/signing-trust.json"
    assert json.loads(state.read_text())["active_key"] == public.decode()
    helper.write_text(original)
    installer.run_installer("--resume")
    assert installer.state()["phase"] == "complete"
    assert (installer.root / "etc/robopark/release-public-key.pem").read_bytes() == public
    projection = installer.root / "var/lib/robopark/ops/public/signing-trust.json"
    assert json.loads(projection.read_text())["active_key"] == public.decode()


@pytest.mark.parametrize("version", ["1.0.0", "0.9.0", "2.0.0"])
def test_old_key_bundle_cannot_replace_or_downgrade_after_rotation(installer, version):
    public = bridge_bundle(installer)
    installer.run_installer()
    current = (installer.root / "opt/robopark/current").resolve()
    before = (current / "manifest.json").read_bytes()
    installer.write_release(version)  # same trusted old signer, different manifest
    installer.run_installer("--resume", success=False)
    assert (installer.root / "etc/robopark/release-public-key.pem").read_bytes() == public
    assert (current / "manifest.json").read_bytes() == before
    assert (installer.root / "opt/robopark/current").resolve() == current
    assert sorted(p.name for p in current.parent.iterdir()) == ["1.0.0"]


@pytest.mark.parametrize("change", ["bundle_key", "pin", "installed_manifest"])
def test_historical_resume_requires_exact_bundle_and_private_pin(installer, change):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    public = bridge_bundle(installer)
    installer.run_installer()
    current = (installer.root / "opt/robopark/current").resolve()
    if change == "bundle_key":
        installer.key = Ed25519PrivateKey.generate()
        (installer.bundle / "keys/release-public-key.pem").write_bytes(
            installer.key.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )
        installer.write_release()
    elif change == "pin":
        state = installer.root / "var/lib/robopark/ops/state/signing-trust.json"
        value = json.loads(state.read_text())
        value["pins"][current.name]["sha256"] = "0" * 64
        state.write_text(json.dumps(value))
    else:
        manifest = current / "manifest.json"
        manifest.write_bytes(manifest.read_bytes() + b"\n")
    installer.run_installer("--resume", success=False)
    assert (installer.root / "etc/robopark/release-public-key.pem").read_bytes() == public
    assert (installer.root / "opt/robopark/current").resolve() == current
