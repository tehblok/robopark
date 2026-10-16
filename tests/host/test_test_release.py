from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from robopark_host.runtime import production_config
from robopark_ota.host_install import write_host_configuration

from scripts import build_test_release
from scripts.build_test_release import (
    ReleaseBuildError,
    require_committed_source,
    require_compatible_from_version,
    require_reviewed_workspace,
    stage_release_source,
    write_delivery_metadata,
)


@pytest.fixture(autouse=True)
def fake_pkg_embedded_update_root(monkeypatch):
    def embedded(package: Path) -> bytes:
        document = json.loads((package.parent / "update-root.json").read_text())
        return base64.b64decode(document["ed25519"], validate=True)

    monkeypatch.setattr(build_test_release, "_embedded_macos_update_root", embedded)


def _host_env(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if line and not line.startswith("#")
    )


def _signed_mac_assets(
    directory: Path,
    version: str,
    *,
    channel: str = "rc",
    catalog_overrides: dict | None = None,
    package_bytes: bytes = b"mac-release",
) -> tuple[Path, Path]:
    package = directory / f"Robopark-{version}-arm64.pkg"
    package.write_bytes(package_bytes)
    document = {
        "schema": 1,
        "generation": 2026101301,
        "expires": 4102444800,
        "channels": {
            channel: {
                "version": version,
                "url": f"/api/distribution/v1/macos/{package.name}",
                "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
                "length": package.stat().st_size,
                "architecture": "arm64",
                "protocol": 1,
                "notes": "test",
            }
        },
    }
    document.update(catalog_overrides or {})
    payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    private = Ed25519PrivateKey.generate()
    (directory / "catalog.json").write_text(
        json.dumps(
            {
                "payload": base64.b64encode(payload).decode(),
                "signature": base64.b64encode(private.sign(payload)).decode(),
            }
        )
    )
    root = directory / "update-root.json"
    root.write_text(
        json.dumps(
            {
                "ed25519": base64.b64encode(
                    private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
                ).decode()
            }
        )
    )
    return package, root


@pytest.mark.parametrize(
    ("environment", "bridge_enabled"),
    [("test", "true"), ("production", "false")],
)
def test_clean_install_persists_role_identity_and_safe_bridge_defaults(
    tmp_path: Path, environment: str, bridge_enabled: str
):
    host_env = write_host_configuration(
        tmp_path,
        memory_mb=8192,
        cpu_count=4,
        public_origin=f"https://{environment}.example.test",
        environment=environment,
        instance_id="50b4ae12-c0d8-4c76-9639-2984f164408b",
    )

    values = _host_env(host_env)
    assert values["ROBOPARK_ENVIRONMENT"] == environment
    assert values["ROBOPARK_INSTANCE_ID"] == "50b4ae12-c0d8-4c76-9639-2984f164408b"
    assert values["EMERGENCY_BRIDGE_ENABLED"] == bridge_enabled
    assert values["EMERGENCY_BRIDGE_URL"] == "http://emergency-bridge:8091"
    assert values["EMERGENCY_DIRECT_FALLBACK_ENABLED"] == bridge_enabled
    assert values["MAC_CORPORATE_GATEWAY_ENABLED"] == bridge_enabled
    assert values["MAC_CORPORATE_SERVER_FALLBACK_ENABLED"] == "true"


def test_generated_instance_identity_is_a_canonical_uuid(tmp_path: Path):
    values = _host_env(
        write_host_configuration(
            tmp_path,
            memory_mb=32768,
            cpu_count=8,
            public_origin="https://test.example.test",
            environment="test",
        )
    )

    assert str(UUID(values["ROBOPARK_INSTANCE_ID"])) == values["ROBOPARK_INSTANCE_ID"]


@pytest.mark.parametrize(
    "public_origin", ["https://prod.example.test/", "https://prod.example.test:443/"]
)
def test_test_install_rejects_production_origin_before_writing_config(
    tmp_path: Path, public_origin
):
    with pytest.raises(ValueError, match="test_origin_must_be_isolated"):
        write_host_configuration(
            tmp_path,
            memory_mb=8192,
            cpu_count=4,
            public_origin=public_origin,
            environment="test",
            production_origin="https://prod.example.test",
        )

    assert not (tmp_path / "etc/robopark/host.env").exists()


def test_test_role_cannot_activate_production_preset(tmp_path: Path):
    from robopark_ota.cli import _clean_install

    with pytest.raises(ValueError, match="test_environment_forbids_production_preset"):
        _clean_install(
            tmp_path / "not-opened.ota",
            tmp_path / "host",
            preset="robopark",
            environment="test",
        )


@pytest.mark.parametrize("bridge_enabled", [False, True])
def test_installed_compose_keeps_single_process_private_bridge(
    host_paths, bridge_enabled
):
    host_paths.etc.mkdir(parents=True)
    (host_paths.etc / "host.env").write_text(
        f"UVICORN_WORKERS=2\nEMERGENCY_BRIDGE_ENABLED={str(bridge_enabled).lower()}\n"
    )
    proc = host_paths.root / "proc"
    proc.mkdir()
    (proc / "meminfo").write_text(f"MemTotal: {8 * 1024 * 1024} kB\n")
    (proc / "cpuinfo").write_text("processor : 0\n" * 4)
    release = host_paths.releases / "test"
    document = {
        "services": {
            "api": {"build": {"context": "api"}, "environment": {}},
            "worker": {"environment": {}},
            "web": {"build": {"context": "web"}, "depends_on": {}},
            "emergency-bridge": {
                "command": ["python", "-m", "robopark_api.emergency_bridge"],
                "environment": {},
            },
        }
    }

    rendered = production_config(document, host_paths, release, "candidate")

    bridge = rendered["services"]["emergency-bridge"]
    assert bridge["image"] == rendered["services"]["api"]["image"]
    assert bridge["command"] == ["python", "-m", "robopark_api.emergency_bridge"]
    assert bridge["environment"]["DATABASE_URL"].endswith("@db:5432/robopark")
    assert bridge["env_file"] == [str(host_paths.etc / "host.env")]
    assert bridge["depends_on"] == {"db": {"condition": "service_healthy"}}
    assert "ports" not in bridge
    assert bridge["read_only"] is True
    assert bridge["tmpfs"] == ["/tmp:size=32m,mode=1777"]
    assert all(mount["target"] != "/host-repo" for mount in bridge["volumes"])
    dependency = rendered["services"]["web"]["depends_on"].get("emergency-bridge")
    assert dependency == ({"condition": "service_healthy"} if bridge_enabled else None)


def test_public_agent_websocket_routes_only_to_private_broker():
    nginx = Path("apps/web/nginx.conf").read_text()
    match = re.search(
        r"location = /api/emergency/bridge/ws \{(?P<body>.*?)\n    \}",
        nginx,
        re.DOTALL,
    )

    assert match is not None
    body = match.group("body")
    assert "proxy_pass http://robopark_emergency_bridge/emergency/bridge/ws;" in body
    assert "proxy_set_header Upgrade $http_upgrade;" in body
    assert 'proxy_set_header Connection "upgrade";' in body
    assert 'proxy_set_header Connection "";' not in body


def test_reviewed_workspace_accepts_staged_changes_but_rejects_unstaged_or_untracked(
    tmp_path: Path,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.test"], cwd=repository, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repository, check=True)
    source = repository / "tracked.txt"
    source.write_text("base\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repository, check=True)

    source.write_text("reviewed\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repository, check=True)
    require_reviewed_workspace(repository)

    source.write_text("not reviewed\n")
    with pytest.raises(ReleaseBuildError, match="unstaged_changes"):
        require_reviewed_workspace(repository)

    subprocess.run(["git", "checkout", "--", "tracked.txt"], cwd=repository, check=True)
    (repository / "scripts").mkdir()
    (repository / "scripts/untracked.py").write_text("not reviewed\n")
    with pytest.raises(ReleaseBuildError, match="untracked_files"):
        require_reviewed_workspace(repository)


def test_release_requires_explicit_reviewed_compatible_source_version(tmp_path: Path):
    repository = tmp_path / "repository"
    metadata = repository / "deploy/release-metadata.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        json.dumps({"compatible_from_versions": ["0.2.3-rc.15", "0.2.3-rc.16"]})
    )

    require_compatible_from_version(repository, "0.2.3-rc.15")

    with pytest.raises(ReleaseBuildError, match="incompatible_from_version"):
        require_compatible_from_version(repository, "0.2.3-rc.14")
    assert json.loads(metadata.read_text())["compatible_from_versions"] == [
        "0.2.3-rc.15",
        "0.2.3-rc.16",
    ]


def test_release_builder_requires_from_version_argument(tmp_path: Path):
    with pytest.raises(SystemExit) as error:
        build_test_release.main([str(tmp_path / "release"), "--skip-gate"])

    assert error.value.code == 2


def test_delivery_metadata_binds_server_and_optional_mac_artifacts(tmp_path: Path):
    output = tmp_path / "release"
    output.mkdir()
    ota = output / "robopark-test.ota"
    ota.write_bytes(b"server-release")
    version = "0.2.0-rc.44.dev7114536505405942333"
    package, update_root = _signed_mac_assets(tmp_path, version)

    manifest_path = write_delivery_metadata(
        output,
        version=version,
        server_ota=ota,
        macos_pkg=package,
        macos_update_root=update_root,
        source={
            "base_commit": "a" * 40,
            "reviewed_tree": "b" * 40,
            "kind": "reviewed-index",
        },
    )

    manifest = json.loads(manifest_path.read_text())
    assert manifest["version"] == version
    assert manifest["server"]["platforms"] == ["khadas-vim4", "nvidia-agx-orin"]
    assert manifest["server"]["sha256"] == hashlib.sha256(b"server-release").hexdigest()
    assert manifest["source"] == {
        "base_commit": "a" * 40,
        "reviewed_tree": "b" * 40,
        "kind": "reviewed-index",
    }
    assert manifest["macos"]["path"] == package.name
    assert manifest["macos"]["catalog"]["path"] == "catalog.json"
    assert manifest["macos"]["update_root"]["path"] == "update-root.json"
    assert (output / package.name).read_bytes() == b"mac-release"
    checksums = (output / "SHA256SUMS").read_text().splitlines()
    assert len(checksums) == 4
    assert checksums == sorted(checksums, key=lambda row: row.split("  ", 1)[1])


def test_one_file_installer_is_built_from_the_signed_package_and_hash_listed(
    tmp_path: Path,
):
    output = tmp_path / "release"
    output.mkdir()
    ota = output / "server.ota"
    ota.write_bytes(b"server")
    package, root = _signed_mac_assets(
        tmp_path, "0.2.3-rc.2", package_bytes=b"xar!test-payload"
    )
    manifest = json.loads(
        write_delivery_metadata(
            output,
            version="0.2.3-rc.2",
            server_ota=ota,
            macos_pkg=package,
            macos_update_root=root,
            include_macos_command=True,
        ).read_text()
    )
    installer = manifest["macos"]["installer"]
    command = output / installer["path"]
    assert command.name == "Robopark-0.2.3-rc.2-arm64.command"
    assert hashlib.sha256(command.read_bytes()).hexdigest() == installer["sha256"]
    assert (
        f"{installer['sha256']}  {command.name}" in (output / "SHA256SUMS").read_text()
    )
    verified = subprocess.run(
        ["bash", str(command), "--verify"], capture_output=True, text=True, check=False
    )
    assert verified.returncode == 0, verified.stderr
    assert hashlib.sha256(package.read_bytes()).hexdigest() in verified.stdout


def test_mac_assets_are_added_only_inside_release_snapshot(tmp_path: Path):
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.test"], cwd=repository, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repository, check=True)
    (repository / "VERSION").write_text("1.2.3\n")
    subprocess.run(["git", "add", "VERSION"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repository, check=True)
    package, update_root = _signed_mac_assets(tmp_path, "1.2.3")
    snapshot = tmp_path / "snapshot"

    stage_release_source(
        repository,
        snapshot,
        macos_pkg=package,
        macos_update_root=update_root,
        version="1.2.3",
    )

    assets = snapshot / "apps/api/src/robopark_api/agent_distribution_assets"
    assert (assets / package.name).read_bytes() == b"mac-release"
    assert (assets / "catalog.json").is_file()
    staged = subprocess.run(
        ["git", "ls-files"], cwd=snapshot, check=True, capture_output=True, text=True
    ).stdout.splitlines()
    assert (
        f"apps/api/src/robopark_api/agent_distribution_assets/{package.name}" in staged
    )
    assert "apps/api/src/robopark_api/agent_distribution_assets/catalog.json" in staged
    assert (
        subprocess.run(
            ["git", "ls-files", "*agent_distribution_assets*"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        == ""
    )


def test_release_snapshot_preserves_tracked_ignored_sources_without_ignored_secrets(
    tmp_path: Path,
):
    source_root = Path(__file__).resolve().parents[2]
    repository = tmp_path / "repository"
    repository.mkdir()
    required = [
        "deploy/release-metadata.json",
        "deploy/ota/__main__.py",
        "deploy/ota/README.installed.md",
        *(
            str(path.relative_to(source_root))
            for path in (source_root / "deploy/ota/robopark_ota").glob("*.py")
        ),
        *(
            f"deploy/host/robopark_host/{name}.py"
            for name in ("storage_layout", "storage_setup", "storage_watchdog")
        ),
    ]
    for relative in required:
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_root / relative, target)
    (repository / "VERSION").write_text("1.2.3\n")
    (repository / ".gitignore").write_text("data/\n.release-secrets/\n")
    component = repository / "apps/web/src/design-system/data/EntityRow.tsx"
    component.parent.mkdir(parents=True)
    component.write_text("export const EntityRow = 'tracked source';\n")
    secret = repository / ".release-secrets/update.key"
    secret.parent.mkdir()
    secret.write_text("untracked secret\n")
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        ["git", "add", "-f", str(component.relative_to(repository))],
        cwd=repository,
        check=True,
    )
    source_inventory = set(
        subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=repository,
            check=True,
            capture_output=True,
        ).stdout.split(b"\0")
    )

    snapshot = stage_release_source(
        repository,
        tmp_path / "snapshot",
        macos_pkg=None,
        version="1.2.3",
    )
    artifact = build_test_release.build_ota(
        snapshot,
        tmp_path / "out",
        git_sha="a" * 40,
    )

    staged = set(
        subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=snapshot,
            check=True,
            capture_output=True,
        ).stdout.split(b"\0")
    )
    assert staged == source_inventory
    assert b"apps/web/src/design-system/data/EntityRow.tsx" in staged
    assert b".release-secrets/update.key" not in staged
    assert not (snapshot / ".release-secrets/update.key").exists()
    with zipfile.ZipFile(artifact) as archive:
        payload = archive.read("release/apps/web/src/design-system/data/EntityRow.tsx")
        assert payload == b"export const EntityRow = 'tracked source';\n"
        assert all(".release-secrets" not in name for name in archive.namelist())


def test_mac_release_requires_root_and_rejects_fake_signature(tmp_path: Path):
    output = tmp_path / "release"
    output.mkdir()
    ota = output / "server.ota"
    ota.write_bytes(b"server")
    package, update_root = _signed_mac_assets(tmp_path, "1.2.3")

    with pytest.raises(ReleaseBuildError, match="macos_update_root_required"):
        write_delivery_metadata(
            output, version="1.2.3", server_ota=ota, macos_pkg=package
        )

    linked_root = tmp_path / "linked-root.json"
    linked_root.symlink_to(update_root)
    with pytest.raises(ReleaseBuildError, match="invalid_macos_update_root"):
        write_delivery_metadata(
            output,
            version="1.2.3",
            server_ota=ota,
            macos_pkg=package,
            macos_update_root=linked_root,
        )

    envelope = json.loads((tmp_path / "catalog.json").read_text())
    envelope["signature"] = base64.b64encode(b"signature").decode()
    (tmp_path / "catalog.json").write_text(json.dumps(envelope))
    with pytest.raises(ReleaseBuildError, match="invalid_macos_catalog"):
        write_delivery_metadata(
            output,
            version="1.2.3",
            server_ota=ota,
            macos_pkg=package,
            macos_update_root=update_root,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema": 2},
        {"generation": 0},
        {"expires": "never"},
    ],
)
def test_mac_release_rejects_invalid_signed_catalog_contract(
    tmp_path: Path, overrides: dict
):
    output = tmp_path / "release"
    output.mkdir()
    ota = output / "server.ota"
    ota.write_bytes(b"server")
    package, update_root = _signed_mac_assets(
        tmp_path, "1.2.3", catalog_overrides=overrides
    )

    with pytest.raises(ReleaseBuildError, match="invalid_macos_catalog"):
        write_delivery_metadata(
            output,
            version="1.2.3",
            server_ota=ota,
            macos_pkg=package,
            macos_update_root=update_root,
        )


def test_mac_release_rejects_signed_non_rc_channel(tmp_path: Path):
    output = tmp_path / "release"
    output.mkdir()
    ota = output / "server.ota"
    ota.write_bytes(b"server")
    package, update_root = _signed_mac_assets(tmp_path, "1.2.3", channel="test")

    with pytest.raises(ReleaseBuildError, match="invalid_macos_catalog"):
        write_delivery_metadata(
            output,
            version="1.2.3",
            server_ota=ota,
            macos_pkg=package,
            macos_update_root=update_root,
        )


def test_mac_release_rejects_package_with_different_embedded_root(
    tmp_path: Path, monkeypatch
):
    output = tmp_path / "release"
    output.mkdir()
    ota = output / "server.ota"
    ota.write_bytes(b"server")
    package, update_root = _signed_mac_assets(tmp_path, "1.2.3")
    monkeypatch.setattr(
        build_test_release, "_embedded_macos_update_root", lambda _package: b"x" * 32
    )

    with pytest.raises(ReleaseBuildError, match="macos_package_update_root_mismatch"):
        write_delivery_metadata(
            output,
            version="1.2.3",
            server_ota=ota,
            macos_pkg=package,
            macos_update_root=update_root,
        )


def test_release_identity_must_match_committed_source(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    path = repository / "VERSION"
    path.write_text("1.0.0\n")
    subprocess.run(["git", "add", "VERSION"], cwd=repository, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "initial",
        ],
        cwd=repository,
        check=True,
    )
    require_committed_source(repository)
    path.write_text("1.0.1\n")
    subprocess.run(["git", "add", "VERSION"], cwd=repository, check=True)
    with pytest.raises(ReleaseBuildError, match="commit_reviewed_release_before_build"):
        require_committed_source(repository)


@pytest.mark.parametrize(
    "embedded,clean,valid",
    [("expected", True, True), ("old", True, False), ("expected", False, False)],
)
def test_package_source_matches_committed_release(
    monkeypatch, tmp_path, embedded, clean, valid
):
    import plistlib

    def expand(command, **kwargs):
        path = (
            Path(command[-1]) / "Payload/Applications/Robopark.app/Contents/Info.plist"
        )
        path.parent.mkdir(parents=True)
        path.write_bytes(
            plistlib.dumps(
                {"RoboparkSourceCommit": embedded, "RoboparkSourceClean": clean}
            )
        )

    monkeypatch.setattr(build_test_release.subprocess, "run", expand)
    if valid:
        build_test_release.validate_macos_source(tmp_path / "package.pkg", "expected")
    else:
        with pytest.raises(ReleaseBuildError, match="macos_source_commit_mismatch"):
            build_test_release.validate_macos_source(
                tmp_path / "package.pkg", "expected"
            )
