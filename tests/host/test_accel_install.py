import configparser
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    package = source / "robopark_accel"
    units = source / "systemd"
    package.mkdir(parents=True)
    units.mkdir()
    (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "robopark-accel").write_text("print('ready')\n", encoding="utf-8")
    for name in ("robopark-accelerator.service", "robopark-edge-llm.service"):
        (units / name).write_text("[Unit]\nDescription=test\n", encoding="utf-8")
    return source


def _ready_platform(_root: Path) -> dict:
    return {
        "identity": {"matches_recovery": True},
        "platform_ready": True,
        "blockers": [],
    }


def _ready_storage(_root: Path, *, require_layout: bool) -> dict:
    assert require_layout is True
    return {"state": "ready"}


def _dependencies(**changes) -> dict:
    result = {
        "python_modules": {"PIL": True, "numpy": True, "onnx": True},
        "executables": {"gst-launch-1.0": True, "trtexec": True},
        "missing": [],
        "dependencies_present": True,
        "operational": False,
        "acceptance_required": True,
    }
    result.update(changes)
    return result


def test_dry_plan_validates_platform_and_storage_without_mutating_root(tmp_path):
    from deploy.accelerator import install

    root = tmp_path / "target"
    source = _source(tmp_path)
    calls = []

    def storage(root_arg, *, require_layout):
        calls.append((root_arg, require_layout))
        return {"state": "ready"}

    report = install.prepare(
        root=root,
        source=source,
        apply=False,
        inspect_fn=_ready_platform,
        storage_fn=storage,
        dependency_fn=lambda: _dependencies(
            missing=["python:onnx", "executable:trtexec"], operational=False
        ),
    )

    assert report["state"] == "planned"
    assert report["applied"] is False
    assert report["start_requested"] is False
    assert report["dependencies"]["missing"] == [
        "python:onnx",
        "executable:trtexec",
    ]
    assert report["dependencies"]["operational"] is False
    assert len(report["release_id"]) == 64
    assert calls == [(root, True)]
    assert not root.exists()


def test_preflight_failure_stops_before_storage_or_filesystem_changes(tmp_path):
    from deploy.accelerator import install

    root = tmp_path / "target"

    def storage(*_args, **_kwargs):
        pytest.fail("storage must not run for the wrong platform")

    with pytest.raises(install.InstallError, match="platform_preflight_failed"):
        install.prepare(
            root=root,
            source=_source(tmp_path),
            inspect_fn=lambda _root: {
                "identity": {"matches_recovery": False},
                "platform_ready": False,
                "blockers": ["recovery_identity_mismatch"],
            },
            storage_fn=storage,
            dependency_fn=lambda: _dependencies(),
        )
    assert not root.exists()


def test_apply_requires_root_before_creating_any_destination(tmp_path, monkeypatch):
    from deploy.accelerator import install

    root = tmp_path / "target"
    monkeypatch.setattr(install.os, "geteuid", lambda: 1000)

    with pytest.raises(install.InstallError, match="root_required_for_apply"):
        install.prepare(
            root=root,
            source=_source(tmp_path),
            apply=True,
            inspect_fn=_ready_platform,
            storage_fn=_ready_storage,
            dependency_fn=lambda: _dependencies(),
        )
    assert not root.exists()


def test_apply_installs_content_addressed_release_and_does_not_start_by_default(
    tmp_path, monkeypatch
):
    from deploy.accelerator import install

    root = tmp_path / "target"
    source = _source(tmp_path)
    calls = []
    monkeypatch.setattr(install.os, "geteuid", lambda: 0)
    monkeypatch.setattr(install, "_account_ids", lambda _runner: (2345, 10001))

    def runner(argv):
        calls.append(argv)

    report = install.prepare(
        root=root,
        source=source,
        apply=True,
        inspect_fn=_ready_platform,
        storage_fn=_ready_storage,
        dependency_fn=lambda: _dependencies(),
        runner=runner,
    )

    release = root / "opt/robopark-accelerator/releases" / report["release_id"]
    current = root / "opt/robopark-accelerator/current"
    assert report["state"] == "installed"
    assert report["applied"] is True
    assert current.is_symlink()
    assert current.resolve() == release.resolve()
    assert (release / "robopark-accel").read_text() == "print('ready')\n"
    assert (release / "robopark_accel/__init__.py").read_text() == "VALUE = 1\n"
    assert (root / "var/lib/robopark/accelerator").is_dir()
    assert (root / "etc/systemd/system/robopark-accelerator.service").is_file()
    assert (root / "etc/systemd/system/robopark-edge-llm.service").is_file()
    assert calls == [["systemctl", "daemon-reload"]]


def test_apply_start_enables_only_main_accelerator_service(tmp_path, monkeypatch):
    from deploy.accelerator import install

    calls = []
    monkeypatch.setattr(install.os, "geteuid", lambda: 0)
    monkeypatch.setattr(install, "_account_ids", lambda _runner: (2345, 10001))

    report = install.prepare(
        root=tmp_path / "target",
        source=_source(tmp_path),
        apply=True,
        start=True,
        inspect_fn=_ready_platform,
        storage_fn=_ready_storage,
        dependency_fn=lambda: _dependencies(),
        runner=lambda argv: calls.append(argv),
    )

    assert report["started"] is True
    assert calls == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", "--now", "robopark-accelerator.service"],
    ]
    assert not any("robopark-edge-llm.service" in command for command in calls)


def test_existing_release_is_verified_before_reuse(tmp_path, monkeypatch):
    from deploy.accelerator import install

    root = tmp_path / "target"
    source = _source(tmp_path)
    monkeypatch.setattr(install.os, "geteuid", lambda: 0)
    monkeypatch.setattr(install, "_account_ids", lambda _runner: (2345, 10001))
    first = install.prepare(
        root=root,
        source=source,
        apply=True,
        inspect_fn=_ready_platform,
        storage_fn=_ready_storage,
        dependency_fn=lambda: _dependencies(),
        runner=lambda _argv: None,
    )
    installed = (
        root
        / "opt/robopark-accelerator/releases"
        / first["release_id"]
        / "robopark_accel/__init__.py"
    )
    installed.chmod(0o644)
    installed.write_text("VALUE = 'tampered'\n", encoding="utf-8")

    with pytest.raises(install.InstallError, match="release_destination_mismatch"):
        install.prepare(
            root=root,
            source=source,
            apply=True,
            inspect_fn=_ready_platform,
            storage_fn=_ready_storage,
            dependency_fn=lambda: _dependencies(),
            runner=lambda _argv: None,
        )


def test_source_symlinks_are_rejected_before_apply(tmp_path, monkeypatch):
    from deploy.accelerator import install

    source = _source(tmp_path)
    (source / "robopark_accel/__init__.py").unlink()
    (source / "robopark_accel/__init__.py").symlink_to(source / "robopark-accel")
    monkeypatch.setattr(install.os, "geteuid", lambda: 0)

    with pytest.raises(install.InstallError, match="source_payload_unsafe"):
        install.prepare(
            root=tmp_path / "target",
            source=source,
            apply=True,
            inspect_fn=_ready_platform,
            storage_fn=_ready_storage,
            dependency_fn=lambda: _dependencies(),
        )


def test_invalid_python_payload_fails_before_destination_mutation(tmp_path, monkeypatch):
    from deploy.accelerator import install

    source = _source(tmp_path)
    (source / "robopark-accel").write_text("def broken(:\n", encoding="utf-8")
    root = tmp_path / "target"
    monkeypatch.setattr(install.os, "geteuid", lambda: 0)

    with pytest.raises(install.InstallError, match="source_python_invalid"):
        install.prepare(
            root=root,
            source=source,
            apply=True,
            inspect_fn=_ready_platform,
            storage_fn=_ready_storage,
            dependency_fn=lambda: _dependencies(),
        )
    assert not root.exists()


def test_dependency_probe_requires_pillow_for_hardware_media_and_real_smoke(
    monkeypatch,
):
    from deploy.accelerator import install

    monkeypatch.setattr(
        install.importlib.util,
        "find_spec",
        lambda name: None if name == "PIL" else SimpleNamespace(),
    )
    monkeypatch.setattr(install, "_find_executable", lambda _name: "/usr/bin/tool")

    report = install.probe_dependencies()

    assert report["capabilities"] == {
        "tensorrt": True,
        "hardware_media": False,
        "software_media": False,
    }
    assert report["dependencies_present"] is False
    assert report["operational"] is False
    assert report["acceptance_required"] is True


def test_existing_root_service_account_is_rejected():
    from deploy.accelerator import install

    original = install.pwd.getpwnam
    install.pwd.getpwnam = lambda _name: SimpleNamespace(pw_uid=0)
    try:
        with pytest.raises(install.InstallError, match="service_account_must_be_nonroot"):
            install._account_ids(lambda _argv: pytest.fail("must not create another account"))
    finally:
        install.pwd.getpwnam = original


def test_new_service_account_gets_video_and_render_groups(monkeypatch):
    from deploy.accelerator import install

    answers = iter((KeyError(), SimpleNamespace(pw_uid=2345)))

    def account(_name):
        value = next(answers)
        if isinstance(value, Exception):
            raise value
        return value

    calls = []
    monkeypatch.setattr(install.pwd, "getpwnam", account)

    assert install._account_ids(lambda argv: calls.append(argv)) == (2345, 10001)
    assert calls[0][calls[0].index("--groups") + 1] == "video,render"


def test_apply_rejects_symlinked_destination_ancestor_before_account_mutation(
    tmp_path, monkeypatch
):
    from deploy.accelerator import install

    root = tmp_path / "target"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "opt").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(install.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        install,
        "_account_ids",
        lambda _runner: pytest.fail("destination guard must run before useradd"),
    )

    with pytest.raises(install.InstallError, match="destination_ancestor_unsafe"):
        install.prepare(
            root=root,
            source=_source(tmp_path),
            apply=True,
            inspect_fn=_ready_platform,
            storage_fn=_ready_storage,
            dependency_fn=lambda: _dependencies(),
        )
    assert list(outside.iterdir()) == []


def _unit(name: str) -> configparser.ConfigParser:
    path = Path(__file__).resolve().parents[2] / "deploy/accelerator/systemd" / name
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read(path)
    assert parser.sections(), f"missing unit {name}"
    return parser


def test_accelerator_unit_runs_unprivileged_with_bounded_writes_and_devices():
    unit = _unit("robopark-accelerator.service")
    service = unit["Service"]
    assert service["User"] == "robopark-accel"
    assert service["Group"] == "10001"
    assert service["ExecStart"] == (
        "/usr/bin/python3 -I /opt/robopark-accelerator/current/robopark-accel serve"
    )
    assert service["ProtectSystem"] == "strict"
    assert set(service["ReadWritePaths"].split()) == {
        "/var/lib/robopark/accelerator",
        "/run/robopark-accel",
    }
    assert service["MemoryMax"] == "4G"
    assert service["MemorySwapMax"] == "0"
    assert service["TasksMax"] == "128"
    raw = Path(__file__).resolve().parents[2].joinpath(
        "deploy/accelerator/systemd/robopark-accelerator.service"
    ).read_text()
    for device in (
        "/dev/nvmap",
        "/dev/nvhost-ctrl",
        "/dev/nvhost-ctrl-gpu",
        "/dev/nvhost-vic",
        "/dev/nvhost-nvjpg",
        "/dev/nvhost-ctrl-nvdla0",
        "/dev/nvhost-nvdla0",
    ):
        assert f"DeviceAllow={device} rw" in raw


def test_edge_unit_is_local_only_and_has_no_automatic_install_target():
    unit = _unit("robopark-edge-llm.service")
    service = unit["Service"]
    assert service["User"] == "robopark-accel"
    assert service["ExecStart"] == (
        "/var/lib/robopark/accelerator/edge-venv/bin/python -I "
        "/opt/robopark-accelerator/current/robopark-accel edge-serve"
    )
    assert service["Environment"] == "ROBOPARK_EDGE_HOST=127.0.0.1 ROBOPARK_EDGE_PORT=18082"
    assert service["ProtectSystem"] == "strict"
    assert service["MemoryMax"] == "16G"
    assert service["MemorySwapMax"] == "0"
    assert "Install" not in unit
    raw = Path(__file__).resolve().parents[2].joinpath(
        "deploy/accelerator/systemd/robopark-edge-llm.service"
    ).read_text()
    assert "ConditionPathExists=/var/lib/robopark/accelerator/edge-profile.json" in raw
    assert "ConditionPathExists=/var/lib/robopark/accelerator/edge-api-key" in raw


def test_cli_defaults_to_read_only_plan(tmp_path, monkeypatch, capsys):
    from deploy.accelerator import install

    monkeypatch.setattr(install, "inspect_platform", _ready_platform)
    monkeypatch.setattr(install, "require_storage", _ready_storage)
    monkeypatch.setattr(install, "probe_dependencies", lambda: _dependencies())

    assert install.main([
        "--root",
        str(tmp_path / "target"),
        "--source",
        str(_source(tmp_path)),
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["state"] == "planned"
    assert report["applied"] is False
