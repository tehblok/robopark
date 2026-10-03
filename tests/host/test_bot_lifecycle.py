"""Optional Telegram bot lifecycle stays explicit, durable, and data preserving."""

import json
from pathlib import Path

import pytest


def _prepare_host(host_paths):
    host_paths.etc.mkdir(parents=True, exist_ok=True)
    host_paths.state.mkdir(parents=True, exist_ok=True)
    (host_paths.etc / "host.env").write_text("UVICORN_WORKERS=2\n")
    (host_paths.etc / "bot-bridge-key").write_text("test-runtime-key\n")
    (host_paths.etc / "bot-bridge-key").chmod(0o600)
    config = host_paths.state / "compose/current.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps({"services": {"bot": {"image": "sha256:" + "b" * 64}}})
    )
    (host_paths.state / "current-compose.json").symlink_to(config)


def test_production_config_keeps_optional_bot_with_host_owned_paths(host_paths):
    from robopark_host.runtime import production_config

    _prepare_host(host_paths)
    release = host_paths.releases / "1.0.0"
    release.mkdir(parents=True)
    document = {
        "services": {
            "db": {"image": "postgres:17.11-alpine"},
            "api": {"build": {"context": "api"}, "environment": {}},
            "worker": {"environment": {}},
            "web": {"build": {"context": "web"}},
            "bot": {
                "profiles": ["bot"],
                "build": {"context": "bot"},
                "environment": {},
            },
            "ops-agent": {},
        }
    }

    rendered = production_config(document, host_paths, release, "candidate")

    assert set(rendered["services"]) == {"db", "api", "worker", "web", "bot"}
    bot = rendered["services"]["bot"]
    assert bot["profiles"] == ["bot"]
    assert bot["image"] == "robopark-bot:candidate"
    assert bot["build"]["context"] == str(release / "apps/bot")
    assert bot["depends_on"] == {"api": {"condition": "service_healthy"}}
    assert bot["environment"] == {
        "ROBOPARK_API_URL": "http://api:8000",
        "ROBOPARK_BOT_BRIDGE_KEY_FILE": "/run/secrets/bot-bridge-key",
        "ROBOPARK_BOT_DATA_DIR": "/data/telegram-bot",
        "TZ": "Europe/Moscow",
    }
    mounts = {item["target"]: item for item in bot["volumes"]}
    assert mounts["/data"]["source"] == str(host_paths.var / "data")
    assert mounts["/run/secrets/bot-bridge-key"] == {
        "type": "bind",
        "source": str(host_paths.etc / "bot-bridge-key"),
        "target": "/run/secrets/bot-bridge-key",
        "read_only": True,
    }
    api_mounts = {
        item["target"]: item for item in rendered["services"]["api"]["volumes"]
    }
    assert api_mounts["/run/secrets/bot-bridge-key"]["source"] == str(
        host_paths.etc / "bot-bridge-key"
    )


def test_bot_enabled_marker_is_strict_private_and_fail_closed(host_paths):
    from robopark_host.runtime import bot_enabled

    _prepare_host(host_paths)
    marker = host_paths.var / "data/telegram-bot/enabled.json"
    marker.parent.mkdir(parents=True)

    assert bot_enabled(host_paths) is False
    marker.write_text('{"schema": 1, "enabled": true}\n')
    marker.chmod(0o600)
    assert bot_enabled(host_paths) is True

    marker.write_text('{"schema": 1, "enabled": "yes"}\n')
    with pytest.raises(ValueError, match="invalid_bot_enabled_state"):
        bot_enabled(host_paths)

    marker.write_text('{"schema": 1, "enabled": false, "enabled": true}\n')
    with pytest.raises(ValueError, match="invalid_bot_enabled_state"):
        bot_enabled(host_paths)

    marker.write_text('{"schema": 1, "enabled": false}\n')
    marker.chmod(0o644)
    with pytest.raises(ValueError, match="invalid_bot_enabled_state"):
        bot_enabled(host_paths)


def test_reconcile_starts_or_stops_only_bot_and_preserves_data(host_paths):
    from robopark_host.runtime import bot_compose_command

    _prepare_host(host_paths)
    marker = host_paths.var / "data/telegram-bot/enabled.json"
    marker.parent.mkdir(parents=True)
    marker.write_text('{"schema": 1, "enabled": true}\n')
    marker.chmod(0o600)

    enabled = bot_compose_command(host_paths, "reconcile")
    assert enabled[-5:] == ["up", "-d", "--no-build", "--no-deps", "bot"]
    assert "down" not in enabled
    assert "rm" not in enabled

    marker.write_text('{"schema": 1, "enabled": false}\n')
    disabled = bot_compose_command(host_paths, "reconcile")
    assert disabled[-4:] == ["stop", "--timeout", "30", "bot"]
    assert "down" not in disabled
    assert "rm" not in disabled
    assert bot_compose_command(host_paths, "stop")[-1] == "bot"


def test_bot_units_reconcile_on_boot_and_immediate_toggle():
    app = Path("deploy/systemd/robopark.service").read_text()
    path = Path("deploy/systemd/robopark-bot.path").read_text()
    service = Path("deploy/systemd/robopark-bot.service").read_text()
    installer = Path("deploy/installer/lib/install-services.py").read_text()

    assert "Wants=network-online.target robopark-bot.path" in app
    assert "robopark-bot-runtime reconcile" in app
    assert "robopark-bot-runtime stop" in app
    assert "PathChanged=/var/lib/robopark/data/telegram-bot/enabled.json" in path
    assert "PartOf=robopark.service" in path
    assert "robopark-bot-runtime reconcile" in service
    assert '"robopark-bot.service"' in installer
    assert '"robopark-bot.path"' in installer


def test_bot_image_is_recorded_with_exact_release_ownership(host_paths):
    from robopark_host.image_retention import record

    release = host_paths.releases / "1.0.0"
    release.mkdir(parents=True)
    tag = "release-" + "a" * 64
    document = {
        "services": {
            name: {"image": "sha256:" + digit * 64}
            for name, digit in (("api", "1"), ("web", "2"), ("bot", "3"))
        }
    }

    record(host_paths, release, tag, document)

    receipt = json.loads((host_paths.state / "image-owned" / f"{tag}.json").read_text())
    assert receipt["images"] == {
        "api": "sha256:" + "1" * 64,
        "web": "sha256:" + "2" * 64,
        "bot": "sha256:" + "3" * 64,
    }


def test_enabled_bot_is_a_release_readiness_gate(monkeypatch):
    from robopark_host.updater import SystemRunner

    runner = SystemRunner()
    probes = []

    def run_cleanup(argv, *, timeout):
        del timeout
        probes.append(argv)
        return True

    monkeypatch.setattr(runner, "run_cleanup", run_cleanup)
    assert runner.wait_ready(project="robopark", config=Path("/tmp/compose.json"), timeout=1, bot_required=True)
    assert any("bot" in command and "bot-ready" in " ".join(command) for command in probes)


def test_build_service_order_includes_bot_without_parallel_arm_builds():
    from robopark_host.runtime import build_service_config, build_service_names

    document = {
        "services": {
            "db": {"image": "postgres"},
            "api": {"build": {"context": "api"}},
            "web": {"build": {"context": "web"}},
            "bot": {"build": {"context": "bot"}},
            "worker": {"image": "api"},
        }
    }

    assert build_service_names(document) == ("api", "web", "bot")
    assert build_service_config("api", "smoke.json", "production.json") == "smoke.json"
    assert build_service_config("web", "smoke.json", "production.json") == "smoke.json"
    assert (
        build_service_config("bot", "smoke.json", "production.json")
        == "production.json"
    )


def test_legacy_ota_activates_bot_watcher_and_reconciles_after_unit_publish(host_paths):
    from robopark_host.updater import _activate_bot_lifecycle

    candidate = host_paths.releases / "2.0.0"
    (candidate / "deploy/host").mkdir(parents=True)
    calls = []

    class Runner:
        def run(self, argv, **kwargs):
            calls.append((list(map(str, argv)), kwargs))

    _activate_bot_lifecycle(host_paths, candidate, Runner())

    assert calls == [
        (["systemctl", "daemon-reload"], {"timeout": 60}),
        (["systemctl", "start", "robopark-bot.path"], {"timeout": 60}),
        (
            [
                "python3",
                "-I",
                str(candidate / "deploy/host/robopark-bot-runtime"),
                "reconcile",
            ],
            {"timeout": 180},
        ),
    ]
