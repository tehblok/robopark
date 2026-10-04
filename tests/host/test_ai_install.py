import json

from robopark_host.state import atomic_write_json


def _release(host_paths):
    release = host_paths.releases / "ai"
    units = release / "deploy/systemd"
    units.mkdir(parents=True)
    for name in ("robopark-ai-setup.service", "robopark-ai.service", "robopark-ai-broker.service"):
        (units / name).write_text("[Service]\n")
    host_paths.current.symlink_to(release)
    return release


def test_reconcile_compose_mounts_broker_and_public_state_into_api_and_worker(host_paths):
    from robopark_host.ai_install import reconcile_ai_compose

    release = _release(host_paths)
    target = host_paths.state / "compose/current.json"
    before = {
        "x-robopark-release": str(release),
        "services": {
            "api": {"image": "sha256:api", "environment": {}, "volumes": []},
            "worker": {"image": "sha256:api", "environment": {}, "volumes": []},
            "db": {"image": "sha256:db"},
        },
    }
    atomic_write_json(target, before)
    (host_paths.state / "current-compose.json").symlink_to(target)
    assert reconcile_ai_compose(host_paths) is True
    actual = json.loads(target.read_text())
    for name in ("api", "worker"):
        service = actual["services"][name]
        assert service["environment"] == {
            "AI_BROKER_SOCKET": "/run/robopark-ai/broker.sock",
            "AI_RUNTIME_STATE_PATH": "/ops/ai-public/ai-runtime.json",
        }
        assert {v["target"] for v in service["volumes"]} == {"/run/robopark-ai", "/ops/ai-public"}
        state_mount = next(v for v in service["volumes"] if v["target"] == "/ops/ai-public")
        assert state_mount["source"] == str(host_paths.ops / "public")
        assert all(v["read_only"] is True for v in service["volumes"])
    assert actual["services"]["db"] == before["services"]["db"]
    assert reconcile_ai_compose(host_paths) is False


def test_reconcile_installation_never_auto_installs_on_unsupported_host(host_paths, monkeypatch):
    from robopark_host import ai_install

    release = _release(host_paths)
    calls = []
    monkeypatch.setattr(ai_install, "probe_support", lambda paths: (False, "agx_required"))
    monkeypatch.setattr(ai_install, "publish_status", lambda paths, **kw: calls.append(kw))

    class Runner:
        def run(self, argv, **kwargs):
            calls.append(argv)

    ai_install.reconcile_ai_installation(host_paths, release, Runner(), auto_install=True)
    assert calls == [{"supported": False, "reason": "agx_required"}]


def test_ota_preserves_disabled_ai_without_reenabling_installed_model(host_paths, monkeypatch):
    from robopark_host import ai_install, ai_runtime

    release = _release(host_paths)
    state = host_paths.ops / "public/ai-runtime.json"
    atomic_write_json(state, {"schema": 1, "enabled": True}, mode=0o644)
    ai_runtime.write_enabled_intent(host_paths, False)
    monkeypatch.setattr(ai_install, "probe_support", lambda paths: (True, None))
    monkeypatch.setattr(ai_runtime, "installed", lambda paths, **kwargs: True)
    calls = []

    class Runner:
        def run(self, argv, **kwargs):
            calls.append(argv)

    ai_install.reconcile_ai_installation(host_paths, release, Runner(), auto_install=False)
    assert not any(
        call[:3] == ["systemctl", "enable", "--now"]
        and "robopark-ai.service" in call
        for call in calls
    )
    assert ["systemctl", "disable", "--now", "robopark-ai.service"] in calls
    assert json.loads(state.read_text())["enabled"] is False


def test_ai_native_unit_is_nonroot_cuda_bounded_and_loopback_only():
    from test_systemd_units import unit

    service = unit("robopark-ai.service")["Service"]
    assert service["User"] == "robopark-ai"
    assert service["MemoryMax"] == "16G"
    assert service["ExecStartPre"].endswith("robopark ai-verify")
    assert "--api-key-file /var/lib/robopark/ai/api-key" in service["ExecStart"]
    command = service["ExecStart"]
    assert "--host 127.0.0.1" in command and "--port 18081" in command
    for value in ("-c 8192", "-np 1", "-ngl 99", "-fa on", "--jinja", "--reasoning auto"):
        assert value in command
    assert "--reasoning-format" not in command


def test_boot_bridge_is_created_by_tmpfiles_and_broker_does_not_depend_on_core():
    from test_systemd_units import REPO, unit

    policy = (REPO / "deploy/tmpfiles.d/robopark.conf").read_text()
    assert "d /run/robopark-ai 0750 root 10001 -" in policy
    broker = unit("robopark-ai-broker.service")
    assert "RuntimeDirectory" not in broker["Service"]
    assert "robopark.service" not in broker["Unit"].get("Requires", "")
    assert "robopark.service" in broker["Unit"]["Before"]


def test_production_config_projects_ai_into_api_and_worker(host_paths):
    from robopark_host.runtime import production_config

    host_paths.etc.mkdir(parents=True)
    (host_paths.etc / "host.env").write_text("UVICORN_WORKERS=2\n")
    proc = host_paths.root / "proc"
    proc.mkdir()
    (proc / "meminfo").write_text(f"MemTotal: {32 * 1024**2} kB\n")
    (proc / "cpuinfo").write_text("processor : 0\n" * 12)
    device_tree = proc / "device-tree"
    device_tree.mkdir()
    (device_tree / "model").write_bytes(b"NVIDIA Jetson AGX Orin\0")
    (device_tree / "compatible").write_bytes(b"nvidia,p3701-0000\0nvidia,tegra234\0")
    release = _release(host_paths)
    document = {"services": {"api": {"build": {}, "environment": {}}, "worker": {}, "web": {"build": {}}}}
    result = production_config(document, host_paths, release, "test")
    for name in ("api", "worker"):
        service = result["services"][name]
        assert service["environment"]["AI_BROKER_SOCKET"] == "/run/robopark-ai/broker.sock"
        assert service["environment"]["AI_RUNTIME_STATE_PATH"] == "/ops/ai-public/ai-runtime.json"
        assert {v["target"] for v in service["volumes"] if isinstance(v, dict)} >= {"/run/robopark-ai", "/ops/ai-public"}
    assert result["services"]["api"]["mem_limit"] == "4g"
    assert result["services"]["db"]["mem_limit"] == "4g"
    assert result["services"]["worker"]["mem_limit"] == "1g"


def test_migration_config_removes_optional_runtime_mounts_without_mutating_production(host_paths):
    from robopark_host.ai_install import migration_compose

    production = host_paths.state / "compose/op-production.json"
    document = {"services": {"api": {"volumes": [
        {"type": "bind", "source": "/run/robopark-ai", "target": "/run/robopark-ai"},
        {"type": "bind", "source": "/state", "target": "/ops/ai-public"},
        {"type": "bind", "source": "/data", "target": "/data"},
    ]}}}
    atomic_write_json(production, document)
    migration = migration_compose(host_paths, production, "op")
    assert migration != production
    assert json.loads(migration.read_text())["services"]["api"]["volumes"] == [
        {"type": "bind", "source": "/data", "target": "/data"}
    ]
    assert json.loads(production.read_text()) == document
