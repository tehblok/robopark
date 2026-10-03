"""A host upgrade must admit the optional terminal as one unit, API-only."""

import importlib.util
import json
from pathlib import Path

import pytest

NAMES = (
    "robopark-terminal-setup.service",
    "robopark-terminal-broker.service",
    "robopark-terminal-maintenance@.service",
    "robopark-terminal-root@.service",
)


def terminal_module():

    assert importlib.util.find_spec("robopark_host.terminal_install"), (
        "terminal lifecycle support missing"
    )
    from robopark_host import terminal_install

    return terminal_install


def payload(release):
    d = release / "deploy/systemd"
    d.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        (d / name).write_text("[Service]\nExecStart=/bin/false\n")


def test_foundation_without_terminal_units_is_supported(tmp_path):
    assert terminal_module().terminal_payload_present(tmp_path) is False


def test_partial_terminal_payload_is_rejected(tmp_path):
    from robopark_host.release import ReleaseError

    (tmp_path / "deploy/systemd").mkdir(parents=True)
    (tmp_path / "deploy/systemd" / NAMES[0]).write_text("partial")
    with pytest.raises(ReleaseError, match="terminal_payload_invalid"):
        terminal_module().terminal_payload_present(tmp_path)


def test_symlinked_terminal_unit_is_rejected(tmp_path):
    from robopark_host.release import ReleaseError

    payload(tmp_path)
    f = tmp_path / "deploy/systemd" / NAMES[0]
    f.unlink()
    f.symlink_to("/dev/null")
    with pytest.raises(ReleaseError, match="terminal_payload_invalid"):
        terminal_module().terminal_payload_present(tmp_path)


def test_only_api_receives_terminal_socket_mount(host_paths):
    from robopark_host.runtime import production_config

    host_paths.etc.mkdir(parents=True)
    (host_paths.etc / "host.env").write_text("UVICORN_WORKERS=2\n")
    release = host_paths.releases / "feature"
    payload(release)
    document = {
        "services": {
            "api": {"build": {}, "environment": {}},
            "web": {"build": {}},
            "worker": {},
        }
    }
    result = production_config(document, host_paths, release, "test")
    mounts = result["services"]["api"]["volumes"]
    assert {
        "type": "bind",
        "source": str(host_paths.root / "run/robopark-terminal"),
        "target": "/run/robopark-terminal",
        "read_only": True,
        "bind": {"create_host_path": False},
    } in mounts
    for name in ("worker", "web", "db"):
        assert "/run/robopark-terminal" not in json.dumps(result["services"][name])


def test_ota_render_detects_terminal_in_staging_before_candidate_exists(
    host_paths, monkeypatch
):
    from uuid import uuid4

    from robopark_host import runtime, updater

    identity = str(uuid4())
    candidate = f"feature-{identity}"
    stage = host_paths.releases / f".staging-{identity}"
    payload(stage)
    host_paths.etc.mkdir(parents=True)
    (host_paths.etc / "host.env").write_text("UVICORN_WORKERS=2\n")
    document = {
        "services": {
            "api": {"build": {"context": str(stage / "apps/api")}, "environment": {}},
            "web": {"build": {"context": str(stage / "apps/web")}},
            "worker": {},
        }
    }

    class Runner:
        def run(self, *_args, **_kwargs):
            return json.dumps(document)

    monkeypatch.setattr(runtime, "source_compose_environment", lambda _paths: {})
    assert not (host_paths.releases / candidate).exists()
    _, smoke_path = updater._render_configs(
        host_paths, {"job_id": identity, "candidate": candidate}, Runner(), stage
    )
    production = json.loads(
        (host_paths.state / "compose" / f"{identity}-production.json").read_text()
    )
    api = production["services"]["api"]
    assert (
        api["environment"].get("TERMINAL_BROKER_SOCKET")
        == "/run/robopark-terminal/broker.sock"
    )
    assert any(v.get("target") == "/run/robopark-terminal" for v in api["volumes"])
    assert api["build"]["context"] == str(host_paths.releases / candidate / "apps/api")
    for name in ("worker", "web", "db"):
        assert "/run/robopark-terminal" not in json.dumps(production["services"][name])
    assert "/run/robopark-terminal" not in smoke_path.read_text()
    assert not (host_paths.releases / candidate).exists()


def test_rollback_removes_optional_units_not_in_snapshot(host_paths):
    from robopark_host.rollback import restore_units

    installed = host_paths.root / "etc/systemd/system"
    installed.mkdir(parents=True)
    for name in NAMES:
        (installed / name).write_text("candidate")
    restore_units(host_paths, {"job_id": "old"})
    assert all(not (installed / name).exists() for name in NAMES)


def test_failed_stop_aborts_terminal_quiesce(host_paths):
    from robopark_host.release import ReleaseError

    d = host_paths.root / "etc/systemd/system"
    d.mkdir(parents=True)
    (d / NAMES[1]).write_text("installed")

    class Runner:
        def run(self, args, **kw):
            if "show" in args:
                return "active\n"
            return ""

    with pytest.raises(ReleaseError, match="terminal_stop_failed"):
        terminal_module().quiesce_terminal(host_paths, Runner(), reason="ota")


def test_migration_does_not_require_uninstalled_terminal_socket(
    host_paths, monkeypatch
):
    from types import SimpleNamespace
    from uuid import uuid4

    from robopark_host import updater
    from robopark_host.ota_update import SystemOtaUpdateRuntime

    identity = str(uuid4())
    path = host_paths.state / "compose" / f"{identity}-production.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "services": {
                    "api": {
                        "volumes": [
                            {
                                "type": "bind",
                                "source": "/run/robopark-terminal",
                                "target": "/run/robopark-terminal",
                            },
                            {"type": "bind", "source": "/data", "target": "/data"},
                        ]
                    }
                }
            }
        )
    )
    seen = []

    class Runner:
        def run(self, args, **kw):
            config = Path(args[args.index("-f") + 1])
            seen.append(json.loads(config.read_text()))

    monkeypatch.setattr(updater, "_verify_database_head", lambda *a: None)
    runtime = SystemOtaUpdateRuntime(host_paths, Runner())
    runtime.migrate(
        SimpleNamespace(operation_id=identity),
        SimpleNamespace(manifest=SimpleNamespace(migration_head="0056_host_terminal")),
    )
    assert seen[0]["services"]["api"]["volumes"] == [
        {"type": "bind", "source": "/data", "target": "/data"}
    ]
    assert "/run/robopark-terminal" in path.read_text()


def test_foundation_builder_includes_terminal_runtime_support():
    from scripts.build_workspace_install import workspace_source_files

    repo = Path(__file__).resolve().parents[2]
    assert Path(
        "deploy/host/robopark_host/terminal_install.py"
    ) in workspace_source_files(repo)


def test_setup_failure_keeps_core_available_but_terminal_disabled(host_paths, tmp_path):
    release = tmp_path / "candidate"
    (release / "deploy/systemd").mkdir(parents=True)
    for name in terminal_module().TERMINAL_UNITS:
        (release / "deploy/systemd" / name).write_text("[Service]\n")

    class Runner:
        def run(self, args, **kwargs):
            if args[:2] == ["systemctl", "restart"]:
                raise RuntimeError("failed")
            return b""

    assert (
        terminal_module().prepare_terminal_installation(host_paths, release, Runner())
        is False
    )
    projection = json.loads(
        (host_paths.ops / "public/terminal-capabilities.json").read_text()
    )
    assert projection["unavailable_reason"] == "terminal_setup_failed"
