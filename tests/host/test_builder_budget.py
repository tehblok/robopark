"""Short tests for bounded BuildKit retention after successful builds."""

from types import SimpleNamespace

from robopark_host import image_retention
from robopark_host.operational_state import read_object
from robopark_host.state import atomic_write_json

OWNED_BUILDER = "robopark-buildkit-" + "a" * 32


def own_builder(host_paths):
    atomic_write_json(
        host_paths.state / "buildkit-builder.json",
        {"schema": 1, "name": OWNED_BUILDER, "driver": "docker-container"},
    )


class Runner:
    def __init__(self):
        self.commands = []

    def run(self, argv, *, timeout, capture=False):
        del capture
        self.commands.append((argv, timeout))
        if argv[:3] == ["docker", "buildx", "inspect"]:
            return f"Name: {OWNED_BUILDER}\nDriver: docker-container\n".encode()
        if argv[:2] == ["docker", "inspect"]:
            return ('["ROBOPARK_BUILDER_OWNER=' + OWNED_BUILDER + '"]').encode()
        return b""


def test_builder_budget_never_prunes_unowned_default_builder(host_paths, monkeypatch):
    monkeypatch.setattr(
        image_retention.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(total=20 * 1024**3, free=0),
    )
    runner = Runner()

    assert image_retention.cleanup_builder_cache(host_paths, runner, force=True) == {
        "attempted": False, "blocked": True,
    }
    assert runner.commands == []
    health = read_object(host_paths.var / "api-ops/host-health.json")
    assert health["builder_cache_budget"]["blocked"] is True


def test_builder_budget_rejects_changed_builder_driver_before_prune(host_paths, monkeypatch):
    own_builder(host_paths)
    monkeypatch.setattr(
        image_retention.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(total=20 * 1024**3, free=0),
    )

    class ReplacedBuilder:
        def __init__(self):
            self.commands = []

        def run(self, argv, **kwargs):
            self.commands.append(argv)
            if argv[:3] == ["docker", "buildx", "inspect"]:
                return f"Name: {OWNED_BUILDER}\nDriver: docker\n".encode()
            return b""

    runner = ReplacedBuilder()
    assert image_retention.cleanup_builder_cache(host_paths, runner, force=True) == {
        "attempted": False, "blocked": True,
    }
    assert not [argv for argv in runner.commands if argv[:3] == ["docker", "buildx", "prune"]]


def test_builder_budget_rejects_same_name_builder_without_robopark_marker(host_paths, monkeypatch):
    own_builder(host_paths)
    monkeypatch.setattr(
        image_retention.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(total=20 * 1024**3, free=0),
    )

    class ReplacedBuilder:
        def __init__(self):
            self.commands = []

        def run(self, argv, **kwargs):
            self.commands.append(argv)
            if argv[:3] == ["docker", "buildx", "inspect"]:
                return f"Name: {OWNED_BUILDER}\nDriver: docker-container\n".encode()
            if argv[:2] == ["docker", "inspect"]:
                return b"[]"
            return b""

    runner = ReplacedBuilder()
    assert image_retention.cleanup_builder_cache(host_paths, runner, force=True) == {
        "attempted": False, "blocked": True,
    }
    assert not [argv for argv in runner.commands if argv[:3] == ["docker", "buildx", "prune"]]


def test_failed_owned_builder_prune_is_visible_to_api_without_exposing_command_error(
    host_paths, monkeypatch
):
    own_builder(host_paths)
    monkeypatch.setattr(
        image_retention.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(total=20 * 1024**3, free=10 * 1024**3),
    )

    class FailingRunner:
        def run(self, argv, *, timeout, capture=False):
            del timeout, capture
            if argv[:3] == ["docker", "buildx", "inspect"]:
                return f"Name: {OWNED_BUILDER}\nDriver: docker-container\n".encode()
            if argv[:2] == ["docker", "inspect"]:
                return ('["ROBOPARK_BUILDER_OWNER=' + OWNED_BUILDER + '"]').encode()
            raise RuntimeError("private docker error")

    result = image_retention.cleanup_builder_cache(
        host_paths, FailingRunner(), force=True
    )

    assert result == {"attempted": True, "blocked": True}
    health = read_object(host_paths.var / "api-ops/host-health.json")
    projection = health["builder_cache_budget"]
    assert projection["blocked"] is True
    assert projection["attempted"] is True
    assert isinstance(projection["checked_at"], float)
    assert "private docker error" not in str(projection)


def test_successful_build_enforces_storage_budget_without_age_filter(host_paths, monkeypatch):
    own_builder(host_paths)
    monkeypatch.setattr(
        image_retention.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(total=20 * 1024**3, free=10 * 1024**3),
    )
    runner = Runner()

    result = image_retention.cleanup_builder_cache(host_paths, runner, force=True)

    assert result == {"attempted": True, "blocked": False}
    assert [argv for argv, _ in runner.commands] == [
        ["docker", "buildx", "inspect", OWNED_BUILDER],
        ["docker", "inspect", "--type", "container", "--format", "{{json .Config.Env}}",
         "buildx_buildkit_" + OWNED_BUILDER + "0"],
        ["docker", "buildx", "prune", "--builder", OWNED_BUILDER, "-f", "--all", "--max-used-space", "2000000000"],
        ["docker", "buildx", "du", "--builder", OWNED_BUILDER, "--format=json"],
    ]


def test_builder_budget_reports_remaining_internal_cache_above_limit(host_paths, monkeypatch):
    own_builder(host_paths)
    monkeypatch.setattr(
        image_retention.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(total=20 * 1024**3, free=10 * 1024**3),
    )

    class LargeInternalCache(Runner):
        def run(self, argv, *, timeout, capture=False):
            if argv[:3] == ["docker", "buildx", "du"]:
                self.commands.append((argv, timeout))
                return (b'{"ID":"internal","Size":3221225472,"Reclaimable":true,'
                        b'"Shared":false,"Mutable":false,"Type":"internal"}')
            return super().run(argv, timeout=timeout, capture=capture)

    runner = LargeInternalCache()
    result = image_retention.cleanup_builder_cache(host_paths, runner, force=True)

    assert result == {"attempted": True, "blocked": True}
    prune = [argv for argv, _ in runner.commands if argv[:3] == ["docker", "buildx", "prune"]]
    assert len(prune) == 1 and "--all" in prune[0]
    assert [argv for argv, _ in runner.commands if argv[:3] == ["docker", "buildx", "du"]]


def test_routine_check_skips_builder_prune_without_disk_pressure(host_paths, monkeypatch):
    monkeypatch.setattr(
        image_retention.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(total=20 * 1024**3, free=10 * 1024**3),
    )
    runner = Runner()

    assert image_retention.cleanup_builder_cache(host_paths, runner) == {
        "attempted": False,
        "blocked": False,
    }
    assert runner.commands == []


def test_post_update_maintenance_runs_budget_without_owned_images(host_paths, monkeypatch):
    own_builder(host_paths)
    monkeypatch.setattr(
        image_retention.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(total=20 * 1024**3, free=10 * 1024**3),
    )
    runner = Runner()

    result = image_retention.maintenance(
        host_paths, runner, enforce_builder_budget=True
    )

    assert result["builder_cache"] == {"attempted": True, "blocked": False}
    assert runner.commands[0][0] == ["docker", "buildx", "inspect", OWNED_BUILDER]
    assert runner.commands[1][0][:2] == ["docker", "inspect"]
    assert runner.commands[2][0] == [
        "docker", "buildx", "prune", "--builder", OWNED_BUILDER, "-f", "--all", "--max-used-space", "2000000000"
    ]
    assert runner.commands[3][0] == ["docker", "buildx", "du", "--builder", OWNED_BUILDER, "--format=json"]


def test_forced_builder_budget_runs_before_image_cleanup_can_exhaust_budget(
    host_paths, monkeypatch
):
    own_builder(host_paths)
    monkeypatch.setattr(
        image_retention.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(total=20 * 1024**3, free=10 * 1024**3),
    )

    def exhaust_budget(paths, runner, *, budget):
        del paths, runner
        while True:
            try:
                budget.timeout(30)
            except TimeoutError:
                return {"blocked": False, "deleted_tags": 0, "pending": True}

    monkeypatch.setattr(image_retention, "cleanup", exhaust_budget)
    runner = Runner()

    result = image_retention.maintenance(
        host_paths, runner, enforce_builder_budget=True
    )

    assert result["builder_cache"] == {"attempted": True, "blocked": False}
    assert len(runner.commands) == 4
    assert runner.commands[0][0] == ["docker", "buildx", "inspect", OWNED_BUILDER]
    assert runner.commands[1][0][:2] == ["docker", "inspect"]
    assert runner.commands[2][0] == [
        "docker", "buildx", "prune", "--builder", OWNED_BUILDER, "-f", "--all", "--max-used-space", "2000000000"
    ]
    assert 0 < runner.commands[2][1] <= 30
    assert runner.commands[3][0] == ["docker", "buildx", "du", "--builder", OWNED_BUILDER, "--format=json"]
