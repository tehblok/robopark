"""Prune only authenticated host-owned obsolete production images."""

import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.fixture
def host(host_paths):
    from robopark_host.state import atomic_write_json

    current = host_paths.releases / "1.0.0"
    current.mkdir(parents=True)
    host_paths.current.symlink_to(current)
    config = host_paths.state / "compose" / "current.json"
    atomic_write_json(config, {"services": {}})
    (host_paths.state / "current-compose.json").symlink_to(config)
    atomic_write_json(
        host_paths.state / "buildkit-builder.json",
        {"schema": 1, "name": "robopark-buildkit-" + "b" * 32, "driver": "docker-container"},
    )
    return SimpleNamespace(paths=host_paths)


def test_four_versions_remove_old_tags_but_preserve_current_rollback_and_foreign(host):
    from robopark_host.image_retention import cleanup, record
    from robopark_host.rollback import atomic_symlink
    from robopark_host.state import atomic_write_json

    foreign = "other-api:foreign"
    runner = Images({foreign: "sha256:" + "f" * 64})
    jobs = []
    previous = host.paths.current.resolve()
    for version in ["2.0.0", "3.0.0", "4.0.0", "5.0.0"]:
        release = host.paths.releases / version
        release.mkdir()
        job = str(uuid4())
        jobs.append(job)
        document = {"services": {}}
        for service in ("api", "web"):
            tag = f"robopark-{service}:{job}"
            digest = "sha256:" + hashlib.sha256(tag.encode()).hexdigest()
            runner.images[tag] = digest
            document["services"][service] = {"image": digest}
        record(host.paths, release, job, document)
        atomic_write_json(
            host.paths.state / "successful-releases" / f"{version}.json",
            {"successful": True},
        )
        atomic_symlink(previous, host.paths.previous)
        atomic_symlink(release, host.paths.current)
        previous = release
        assert not cleanup(host.paths, runner)["blocked"]
    for job in jobs[:2]:
        assert "robopark-api:" + job in runner.removed
        assert "robopark-web:" + job in runner.removed
    for job in jobs[2:]:
        assert "robopark-api:" + job not in runner.removed
        assert "robopark-web:" + job not in runner.removed
        assert (host.paths.state / "image-owned" / (job + ".json")).exists()
    assert foreign in runner.images
    assert all(tag.startswith(("robopark-api:", "robopark-web:")) for tag in runner.removed)


def obsolete_image(host):
    from uuid import uuid4

    from robopark_host.image_retention import record

    tag = str(uuid4())
    document = {
        "services": {
            name: {"image": "sha256:" + hashlib.sha256(f"{tag}-{name}".encode()).hexdigest()}
            for name in ("api", "web")
        }
    }
    record(host.paths, host.paths.releases / "obsolete", tag, document)
    return tag, {
        "robopark-" + name + ":" + tag: service["image"]
        for name, service in document["services"].items()
    }


class Images:
    def __init__(self, images):
        self.images = dict(images)
        self.removed = []
        self.running = []
        self.fail = False
        self.labels = {}

    def run(self, argv, **kwargs):
        assert 0 < kwargs["timeout"] <= 5
        if argv[:3] == ["docker", "image", "ls"]:
            return "\n".join(tag + " " + digest for tag, digest in self.images.items()).encode()
        if argv[:2] == ["docker", "ps"]:
            return b"a" * 64 if self.running else b""
        if argv[:2] == ["docker", "inspect"]:
            return "\n".join(self.running).encode()
        if argv[:3] == ["docker", "image", "inspect"]:
            if argv[-2] == "{{json .Config.Labels}}":
                return json.dumps(self.labels.get(self.images[argv[-1]], {})).encode()
            if argv[-2] == "{{.Id}} {{.Size}}":
                return (self.images[argv[-1]] + " 4096").encode()
            return self.images[argv[-1]].encode()
        assert argv[:3] == ["docker", "image", "rm"]
        if self.fail:
            raise ValueError("SECRET=must-not-appear")
        target = argv[-1]
        if target.startswith("sha256:"):
            tags = [tag for tag, digest in self.images.items() if digest == target]
            if len(tags) > 1:
                raise ValueError("image_has_multiple_tags")
            if tags:
                self.removed.append(tags[0])
                self.images.pop(tags[0])
        else:
            self.removed.append(target)
            self.images.pop(target)
        return b""


class RetaggedAfterInspection(Images):
    def __init__(self, images, target):
        super().__init__(images)
        self.target = target
        self.foreign_digest = "sha256:" + "f" * 64
        self.swapped = False

    def run(self, argv, **kwargs):
        if (argv[:3] == ["docker", "image", "inspect"]
                and argv[-1] == self.target and argv[-2] == "{{.Id}}"
                and not self.swapped):
            observed = super().run(argv, **kwargs)
            self.images[self.target] = self.foreign_digest
            self.swapped = True
            return observed
        return super().run(argv, **kwargs)


def test_owned_image_preview_lists_only_exact_obsolete_tags_without_deleting(host):
    from robopark_host.image_retention import preview_owned_images, record

    obsolete_tag, obsolete = obsolete_image(host)
    current_tag = str(uuid4())
    current_images = {
        service: "sha256:" + digit * 64 for service, digit in (("api", "1"), ("web", "2"))
    }
    record(host.paths, host.paths.current.resolve(), current_tag, {
        "services": {service: {"image": digest} for service, digest in current_images.items()}
    })
    inventory = {**obsolete, **{
        f"robopark-{service}:{current_tag}": digest
        for service, digest in current_images.items()
    }, "foreign-app:latest": "sha256:" + "f" * 64}
    runner = Images(inventory)

    report = preview_owned_images(host.paths, runner)

    assert report["blocked"] is False
    assert [item["tag"] for item in report["planned"]] == [
        "robopark-api:" + obsolete_tag, "robopark-web:" + obsolete_tag,
    ]
    assert all(item["reported_bytes"] == 4096 for item in report["planned"])
    assert report["total_reported_bytes"] == 8192
    assert runner.removed == []
    assert runner.images == inventory


def test_owned_image_preview_blocks_changed_tags_and_keeps_running_images(host):
    from robopark_host.image_retention import preview_owned_images

    tag, inventory = obsolete_image(host)
    runner = Images(inventory)
    runner.running = [inventory["robopark-api:" + tag]]
    report = preview_owned_images(host.paths, runner)
    assert report["blocked"] is False
    assert [item["tag"] for item in report["planned"]] == ["robopark-web:" + tag]

    runner.running = []
    runner.images["robopark-api:" + tag] = "sha256:" + "9" * 64
    assert preview_owned_images(host.paths, runner)["blocked"] is True
    assert runner.removed == []


def test_manual_image_cleanup_uses_exact_one_time_preview(host):
    from robopark_host.commands import SafeProductionTypedHostEffects

    tag, inventory = obsolete_image(host)
    inventory["foreign-app:latest"] = "sha256:" + "f" * 64
    runner = Images(inventory)
    effects = SafeProductionTypedHostEffects(host.paths, runner=runner)
    preview = effects.docker_image_preview(str(uuid4()))

    assert preview["blocked"] is False
    assert preview["plan_id"]
    assert runner.removed == []

    result = effects.docker_image_execute(str(uuid4()), preview["plan_id"])

    assert result["deleted_count"] == 2
    assert [item["tag"] for item in result["deleted"]] == [
        "robopark-api:" + tag, "robopark-web:" + tag,
    ]
    assert runner.images == {"foreign-app:latest": "sha256:" + "f" * 64}
    with pytest.raises(Exception, match="image_plan_changed"):
        effects.docker_image_execute(str(uuid4()), preview["plan_id"])


def test_manual_image_cleanup_rejects_changed_tag_without_removal(host):
    from robopark_host.commands import SafeProductionTypedHostEffects

    tag, inventory = obsolete_image(host)
    runner = Images(inventory)
    effects = SafeProductionTypedHostEffects(host.paths, runner=runner)
    preview = effects.docker_image_preview(str(uuid4()))
    runner.images["robopark-api:" + tag] = "sha256:" + "9" * 64

    with pytest.raises(Exception, match="image_plan_changed"):
        effects.docker_image_execute(str(uuid4()), preview["plan_id"])

    assert runner.removed == []


def test_manual_cleanup_does_not_remove_retagged_foreign_image(host):
    from robopark_host.image_retention import (
        ImageCleanupPartialError,
        remove_previewed_images,
    )

    tag, inventory = obsolete_image(host)
    target = "robopark-api:" + tag
    runner = RetaggedAfterInspection(inventory, target)

    with pytest.raises(ImageCleanupPartialError):
        remove_previewed_images(runner, [{
            "tag": target, "image_id": inventory[target], "reported_bytes": 4096,
        }])
    assert runner.images[target] == runner.foreign_digest
    assert target not in runner.removed


def test_scheduled_cleanup_does_not_remove_retagged_foreign_image(host):
    from robopark_host.image_retention import cleanup

    tag, inventory = obsolete_image(host)
    target = "robopark-api:" + tag
    runner = RetaggedAfterInspection(inventory, target)

    assert cleanup(host.paths, runner)["blocked"] is True
    assert runner.images[target] == runner.foreign_digest
    assert target not in runner.removed
    assert (host.paths.state / "image-owned" / f"{tag}.json").exists()


def test_shared_foreign_image_id_is_excluded_from_preview_and_cleanup(host):
    from robopark_host.image_retention import cleanup, preview_owned_images

    tag, inventory = obsolete_image(host)
    owned_api = "robopark-api:" + tag
    foreign = "foreign-app:latest"
    inventory[foreign] = inventory[owned_api]
    runner = Images(inventory)

    preview = preview_owned_images(host.paths, runner)
    assert [item["tag"] for item in preview["planned"]] == ["robopark-web:" + tag]
    assert preview["unverified_tags"] == 1
    assert cleanup(host.paths, runner)["pending"] is True
    assert runner.images[foreign] == inventory[foreign]
    assert runner.images[owned_api] == inventory[owned_api]
    assert owned_api not in runner.removed


def test_owned_image_records_do_not_block_at_old_256_limit(host):
    from robopark_host.image_retention import require_record_capacity, reserve

    release = host.paths.releases / "2.0.0"
    for _ in range(257):
        reserve(host.paths, release, str(uuid4()))

    require_record_capacity(host.paths)


def test_manual_image_cleanup_reports_partial_result_for_unexpected_runner_error(host):
    from robopark_host.commands import SafeProductionTypedHostEffects
    from robopark_host.image_retention import ImageCleanupPartialError

    tag, inventory = obsolete_image(host)

    class InterruptedImages(Images):
        def run(self, argv, **kwargs):
            if argv[:3] == ["docker", "image", "rm"] and argv[-1] == inventory["robopark-web:" + tag]:
                raise RuntimeError("internal_location_marker")
            return super().run(argv, **kwargs)

    runner = InterruptedImages(inventory)
    effects = SafeProductionTypedHostEffects(host.paths, runner=runner)
    preview = effects.docker_image_preview(str(uuid4()))

    with pytest.raises(ImageCleanupPartialError) as failure:
        effects.docker_image_execute(str(uuid4()), preview["plan_id"])

    assert [item["tag"] for item in failure.value.deleted] == ["robopark-api:" + tag]
    assert failure.value.uncertain_target["tag"] == "robopark-web:" + tag
    assert "internal_location_marker" not in str(failure.value)


def test_manual_image_removal_rejects_foreign_tag_before_any_mutation(host):
    from robopark_host.image_retention import remove_previewed_images

    tag, inventory = obsolete_image(host)
    foreign = "foreign-app:latest"
    inventory[foreign] = "sha256:" + "f" * 64
    runner = Images(inventory)
    planned = [
        {"tag": "robopark-api:" + tag, "image_id": inventory["robopark-api:" + tag],
         "reported_bytes": 4096},
        {"tag": foreign, "image_id": inventory[foreign], "reported_bytes": 4096},
    ]

    with pytest.raises(ValueError, match="invalid_image_plan"):
        remove_previewed_images(runner, planned)

    assert runner.removed == []


class Builder:
    def __init__(self, error=None):
        self.commands = []
        self.error = error

    def run(self, argv, **kwargs):
        self.commands.append((argv, kwargs["timeout"]))
        if self.error:
            raise self.error
        if argv[:3] == ["docker", "buildx", "inspect"]:
            return ("Name: robopark-buildkit-" + "b" * 32
                    + "\nDriver: docker-container\n").encode()
        if argv[:2] == ["docker", "inspect"]:
            return ('["ROBOPARK_BUILDER_OWNER=robopark-buildkit-'
                    + "b" * 32 + '"]').encode()
        return b""


def test_builder_cache_cleanup_is_disk_pressure_bounded(host, monkeypatch):
    from robopark_host import image_retention

    runner = Builder()
    monkeypatch.setattr(
        image_retention.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(total=20 * 1024**3, free=6 * 1024**3),
    )
    assert image_retention.cleanup_builder_cache(host.paths, runner) == {
        "attempted": False,
        "blocked": False,
    }
    assert runner.commands == []

    monkeypatch.setattr(
        image_retention.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(total=20 * 1024**3, free=6 * 1024**3 - 1),
    )
    assert image_retention.cleanup_builder_cache(host.paths, runner) == {
        "attempted": True,
        "blocked": False,
    }
    assert [command for command, _ in runner.commands] == [
        ["docker", "buildx", "inspect", "robopark-buildkit-" + "b" * 32],
        ["docker", "inspect", "--type", "container", "--format", "{{json .Config.Env}}",
         "buildx_buildkit_robopark-buildkit-" + "b" * 32 + "0"],
        [
            "docker", "buildx", "prune", "--builder", "robopark-buildkit-" + "b" * 32,
            "-f", "--all", "--max-used-space", "2000000000",
        ],
        ["docker", "buildx", "du", "--builder", "robopark-buildkit-" + "b" * 32,
         "--format=json"],
    ]


def test_builder_cache_budget_accepts_human_size_from_buildx_du(host):
    from robopark_host import image_retention

    class DecimalSizeBuilder(Builder):
        def run(self, argv, **kwargs):
            if argv[:3] == ["docker", "buildx", "du"]:
                self.commands.append((argv, kwargs["timeout"]))
                return json.dumps({
                    "ID": "ownedrecord", "Size": "8.192kB",
                    "Reclaimable": True, "Shared": False,
                }).encode()
            return super().run(argv, **kwargs)

    result = image_retention.cleanup_builder_cache(
        host.paths, DecimalSizeBuilder(), force=True
    )
    assert result == {"attempted": True, "blocked": False}


@pytest.mark.parametrize("size,blocked", [(2_000_000_000, False), (2_000_000_001, True)])
def test_builder_budget_requests_the_same_byte_limit_that_it_verifies(host, size, blocked):
    from robopark_host import image_retention

    class MeasuredBuilder(Builder):
        def run(self, argv, **kwargs):
            if argv[:3] == ["docker", "buildx", "du"]:
                return json.dumps({
                    "ID": "ownedrecord", "Size": size,
                    "Reclaimable": True, "Shared": False,
                }).encode()
            return super().run(argv, **kwargs)

    runner = MeasuredBuilder()
    result = image_retention.cleanup_builder_cache(host.paths, runner, force=True)
    prune = next(argv for argv, _ in runner.commands if argv[:3] == ["docker", "buildx", "prune"])
    assert int(prune[prune.index("--max-used-space") + 1]) == 2_000_000_000
    assert result == {"attempted": True, "blocked": blocked}


@pytest.mark.parametrize("error", [TimeoutError(), ValueError("docker failed")])
def test_builder_cache_cleanup_records_runner_failures(host, monkeypatch, error):
    from robopark_host import image_retention

    monkeypatch.setattr(
        image_retention.shutil, "disk_usage", lambda _: SimpleNamespace(free=0)
    )
    result = image_retention.cleanup_builder_cache(host.paths, Builder(error))
    assert result == {"attempted": False, "blocked": True}


def test_running_image_and_foreign_retag_are_preserved_with_diagnostic(host):
    from robopark_host.image_retention import cleanup

    tag, inventory = obsolete_image(host)
    runner = Images(inventory)
    runner.running = [inventory["robopark-api:" + tag]]
    result = cleanup(host.paths, runner)
    assert "robopark-api:" + tag not in runner.removed
    assert result["pending"]
    runner.running = []
    runner.images["robopark-api:" + tag] = "sha256:" + "9" * 64
    assert cleanup(host.paths, runner)["blocked"]
    assert "robopark-api:" + tag not in runner.removed
    assert (host.paths.state / "image-owned" / (tag + ".json")).exists()


def test_image_cleanup_errors_are_sanitized_and_interrupted_removal_retries(host):
    from robopark_host.doctor import _artifact_check
    from robopark_host.image_retention import cleanup

    tag, inventory = obsolete_image(host)
    runner = Images(inventory)
    runner.fail = True
    assert cleanup(host.paths, runner)["blocked"]
    assert _artifact_check(host.paths, None).status == "failed"
    assert "SECRET" not in (host.paths.state / "image-retention.json").read_text()
    runner.fail = False
    # Equivalent to interruption after the first Docker removal and before the receipt.
    runner.images.pop("robopark-api:" + tag)
    assert not cleanup(host.paths, runner)["blocked"]
    assert runner.removed == ["robopark-web:" + tag]
    assert not (host.paths.state / "image-owned" / (tag + ".json")).exists()


@pytest.mark.parametrize("damage", ["json", "symlink", "mode"])
def test_malformed_or_untrusted_image_receipts_never_authorize_removal(host, damage):
    import os

    from robopark_host.image_retention import cleanup

    tag, inventory = obsolete_image(host)
    target = host.paths.state / "image-owned" / (tag + ".json")
    if damage == "json":
        target.write_text("{}")
    elif damage == "mode":
        target.chmod(0o666)
    else:
        original = target.with_suffix(".backup")
        os.replace(target, original)
        target.symlink_to(original)
    runner = Images(inventory)
    assert cleanup(host.paths, runner)["blocked"]
    assert not runner.removed


def test_image_cleanup_has_a_bounded_command_budget_and_resumes(host):
    from robopark_host.image_retention import MAX_COMMANDS, cleanup

    inventory = {}
    for _ in range(12):
        _, images = obsolete_image(host)
        inventory.update(images)
    runner = Images(inventory)
    result = cleanup(host.paths, runner)
    assert result["pending"]
    assert len(runner.removed) <= (MAX_COMMANDS - 2) // 2
    for _ in range(4):
        if not runner.images:
            break
        assert not cleanup(host.paths, runner)["blocked"]
    assert not runner.images


def test_scheduled_image_cleanup_respects_host_operation_owner(host):
    from robopark_host.image_retention import scheduled
    from robopark_host.state import exclusive_lock

    _, inventory = obsolete_image(host)
    runner = Images(inventory)
    with exclusive_lock(host.paths.host_lock):
        assert scheduled(host.paths, runner)["busy"]
    assert not runner.removed


def test_scheduled_cleanup_shares_one_image_and_builder_command_budget(host, monkeypatch):
    from robopark_host import image_retention

    class ScheduledImages(Images):
        def __init__(self, images):
            super().__init__(images)
            self.commands = []

        def run(self, argv, **kwargs):
            self.commands.append((argv, kwargs["timeout"]))
            if argv[:3] == ["docker", "buildx", "inspect"]:
                return ("Name: robopark-buildkit-" + "b" * 32
                        + "\nDriver: docker-container\n").encode()
            if argv[:2] == ["docker", "inspect"]:
                return ('["ROBOPARK_BUILDER_OWNER=robopark-buildkit-'
                        + "b" * 32 + '"]').encode()
            if argv[:3] == ["docker", "buildx", "prune"]:
                return b""
            if argv[:3] == ["docker", "buildx", "du"]:
                return b""
            return super().run(argv, **kwargs)

    _, inventory = obsolete_image(host)
    runner = ScheduledImages(inventory)
    monkeypatch.setattr(image_retention.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))

    result = image_retention.scheduled(host.paths, runner)

    assert len(runner.commands) <= image_retention.MAX_COMMANDS
    assert result["builder_cache"] == {"attempted": True, "blocked": False}
    assert [command for command, _ in runner.commands if command[:3] == ["docker", "buildx", "prune"]]


def test_doctor_handler_reports_blocked_scheduled_builder_cleanup(host, monkeypatch):
    from robopark_host import cli, image_retention
    from robopark_host.checks import DiagnosticReport
    from robopark_host.doctor import _artifact_check

    monkeypatch.setattr(image_retention.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    monkeypatch.setattr("robopark_host.updater.SystemRunner", lambda: Builder(ValueError("docker failed")))
    monkeypatch.setattr(
        cli,
        "run_doctor",
        lambda paths, *_: DiagnosticReport([_artifact_check(paths, None)]),
    )
    reported = []
    monkeypatch.setattr(cli, "_print", reported.append)

    assert cli._doctor_handler(host.paths) == 2
    assert reported[0]["checks"][0]["code"] == "diagnostic_artifacts"
    assert reported[0]["checks"][0]["status"] == "failed"


def test_interrupted_atomic_receipt_temporary_does_not_block_next_cleanup(host):
    from robopark_host.image_retention import cleanup

    tag, inventory = obsolete_image(host)
    (host.paths.state / "image-owned" / ("." + tag + ".json.tmp12345")).write_text("partial")
    assert not cleanup(host.paths, Images(inventory))["blocked"]


def test_failed_candidate_images_are_reclaimed_after_safe_rollback(host):
    from robopark_host.image_retention import cleanup, record

    candidate = host.paths.releases / "2.0.0"
    candidate.mkdir()
    tag = str(uuid4())
    document = {"services": {
        service: {"image": "sha256:" + digit * 64}
        for service, digit in (("api", "1"), ("web", "2"))
    }}
    record(host.paths, candidate, tag, document)
    images = {
        f"robopark-{service}:{tag}": details["image"]
        for service, details in document["services"].items()
    }
    runner = Images(images)

    assert host.paths.current.resolve().name == "1.0.0"
    assert not cleanup(host.paths, runner)["blocked"]
    assert sorted(runner.removed) == sorted(images)
    assert not (host.paths.state / "image-owned" / f"{tag}.json").exists()


def test_hash_ota_staged_candidate_images_survive_scheduled_cleanup(host):
    from robopark_host.image_retention import cleanup, preview_owned_images, record
    from robopark_host.state import atomic_write_json

    operation_id = str(uuid4())
    version = "2.0.0"
    candidate = host.paths.releases / f"{version}-{operation_id}"
    candidate.mkdir()
    document = {"services": {
        service: {"image": "sha256:" + digit * 64}
        for service, digit in (("api", "1"), ("web", "2"))
    }}
    record(host.paths, candidate, operation_id, document)
    images = {
        f"robopark-{service}:{operation_id}": details["image"]
        for service, details in document["services"].items()
    }
    journal = {
        "schema": 1,
        "request": {"operation_id": operation_id, "upload_id": str(uuid4()),
                    "sha256": "a" * 64, "version": version},
        "phase": "staged", "started_at": "2026-09-27T09:00:00+00:00",
        "updated_at": "2026-09-27T09:00:00+00:00", "error": None,
    }
    atomic_write_json(host.paths.state / "ota-update-journal.json", journal)
    runner = Images(images)

    assert preview_owned_images(host.paths, runner)["planned"] == []
    assert cleanup(host.paths, runner) == {"blocked": False, "deleted_tags": 0, "pending": False}
    assert runner.removed == []
    assert (host.paths.state / "image-owned" / f"{operation_id}.json").exists()

    atomic_write_json(host.paths.state / "ota-update-journal.json", {**journal, "phase": "rolled_back"})
    assert cleanup(host.paths, runner)["deleted_tags"] == 2
    assert sorted(runner.removed) == sorted(images)


def test_failed_rollback_preserves_candidate_images_for_manual_recovery(host):
    from robopark_host.image_retention import cleanup, preview_owned_images, record
    from robopark_host.state import atomic_write_json

    operation_id = str(uuid4())
    version = "2.0.0"
    candidate = host.paths.releases / f"{version}-{operation_id}"
    candidate.mkdir()
    document = {"services": {
        service: {"image": "sha256:" + digit * 64}
        for service, digit in (("api", "1"), ("web", "2"))
    }}
    record(host.paths, candidate, operation_id, document)
    images = {
        f"robopark-{service}:{operation_id}": details["image"]
        for service, details in document["services"].items()
    }
    atomic_write_json(
        host.paths.state / "ota-update-journal.json",
        {
            "schema": 1,
            "request": {"operation_id": operation_id, "upload_id": str(uuid4()),
                        "sha256": "a" * 64, "version": version},
            "phase": "failed", "started_at": "2026-09-27T09:00:00+00:00",
            "updated_at": "2026-09-27T09:00:00+00:00",
            "error": "ota_rollback_failed",
        },
    )
    runner = Images(images)

    assert preview_owned_images(host.paths, runner)["planned"] == []
    assert cleanup(host.paths, runner)["deleted_tags"] == 0
    assert runner.removed == []
    assert (host.paths.state / "image-owned" / f"{operation_id}.json").exists()


def test_invalid_hash_ota_journal_blocks_image_cleanup(host):
    from robopark_host.image_retention import cleanup, preview_owned_images
    from robopark_host.state import atomic_write_json

    _, images = obsolete_image(host)
    atomic_write_json(host.paths.state / "ota-update-journal.json", {"phase": "unknown"})
    runner = Images(images)

    assert preview_owned_images(host.paths, runner)["blocked"] is True
    assert cleanup(host.paths, runner)["blocked"] is True
    assert runner.removed == []


def test_reserved_candidate_tags_without_digest_are_not_removed_after_interruption(host):
    from robopark_host.image_retention import cleanup, preview_owned_images, reserve

    candidate = host.paths.releases / "2.0.0"
    candidate.mkdir()
    tag = str(uuid4())
    reserve(host.paths, candidate, tag)
    inventory = {
        f"robopark-{service}:{tag}": "sha256:" + hashlib.sha256(service.encode()).hexdigest()
        for service in ("api", "web")
    }
    runner = Images(inventory)
    preview = preview_owned_images(host.paths, runner)
    assert preview["planned"] == []
    assert preview["unverified_tags"] == 2
    result = cleanup(host.paths, runner)
    assert not result["blocked"]
    assert result["pending"]
    assert runner.removed == []
    assert runner.images == inventory
    assert (host.paths.state / "image-owned" / (tag + ".json")).exists()


def test_terminal_failed_partial_build_keeps_unverified_reserved_candidate_tag(host):
    from robopark_host.image_retention import cleanup, reserve
    from robopark_host.state import atomic_write_json

    tag = str(uuid4())
    version = "2.0.0"
    candidate = host.paths.releases / f"{version}-{tag}"
    candidate.mkdir()
    reserve(host.paths, candidate, tag)
    digest = "sha256:" + "3" * 64
    exact_tag = "robopark-api:" + tag
    foreign_alias = "foreign-app:latest"
    runner = Images({exact_tag: digest, foreign_alias: digest})
    atomic_write_json(
        host.paths.state / "ota-update-receipts" / f"{tag}.json",
        {
            "operation_id": tag,
            "version": version,
            "sha256": "a" * 64,
            "phase": "failed",
            "started_at": "2026-09-27T09:00:00+00:00",
            "finished_at": "2026-09-27T09:01:00+00:00",
            "error": "ota_stage_failed",
        },
    )

    result = cleanup(host.paths, runner)

    assert result == {"blocked": False, "deleted_tags": 0, "pending": True}
    assert runner.removed == []
    assert runner.images == {exact_tag: digest, foreign_alias: digest}
    assert (host.paths.state / "image-owned" / f"{tag}.json").exists()


def test_terminal_failed_labeled_partial_build_removes_exact_orphan(host):
    from robopark_host.image_retention import cleanup, reserve
    from robopark_host.state import atomic_write_json

    tag = str(uuid4())
    version = "2.0.0"
    candidate = host.paths.releases / f"{version}-{tag}"
    candidate.mkdir()
    reserve(host.paths, candidate, tag)
    digest = "sha256:" + "3" * 64
    exact_tag = "robopark-api:" + tag
    runner = Images({exact_tag: digest})
    runner.labels[digest] = {
        "io.robopark.ota.operation-id": tag,
        "io.robopark.ota.release": candidate.name,
    }
    atomic_write_json(
        host.paths.state / "ota-update-receipts" / f"{tag}.json",
        {
            "operation_id": tag, "version": version, "sha256": "a" * 64,
            "phase": "failed", "started_at": "2026-09-27T09:00:00+00:00",
            "finished_at": "2026-09-27T09:01:00+00:00", "error": "ota_stage_failed",
        },
    )

    result = cleanup(host.paths, runner)

    assert result == {"blocked": False, "deleted_tags": 1, "pending": False}
    assert runner.removed == [exact_tag]
    assert not (host.paths.state / "image-owned" / f"{tag}.json").exists()


def test_build_ownership_labels_identify_exact_operation():
    from robopark_host.image_retention import label_build

    build = {"context": "/release/apps/api", "labels": {"example.keep": "yes"}}
    label_build(build, "2.0.0-operation", "operation")

    assert build["labels"] == {
        "example.keep": "yes",
        "io.robopark.ota.operation-id": "operation",
        "io.robopark.ota.release": "2.0.0-operation",
    }


def _legacy_image_journal(tag, candidate, phase):
    return {
        "schema": 1, "job_id": tag, "actor_user_id": 1,
        "candidate": candidate, "previous": "1.0.0",
        "previous_config": "compose/previous.json", "original_previous": None,
        "phase": phase, "migration_started": False,
        "writes_resumed": False, "snapshot_done": False,
        "cutover_started": False, "publication_degraded": False,
        "error": "build_failed" if phase == "failed" else "manual_recovery_required",
    }


@pytest.mark.parametrize("phase,removed", [("failed", True), ("manual_recovery_required", False)])
def test_legacy_failed_build_reconciles_only_when_terminal(host, phase, removed):
    from robopark_host.image_retention import cleanup, reserve
    from robopark_host.state import atomic_write_json

    tag = str(uuid4())
    candidate = host.paths.releases / f"2.0.0-{tag}"
    candidate.mkdir()
    reserve(host.paths, candidate, tag)
    digest = "sha256:" + "4" * 64
    exact_tag = "robopark-api:" + tag
    runner = Images({exact_tag: digest})
    runner.labels[digest] = {
        "io.robopark.ota.operation-id": tag,
        "io.robopark.ota.release": candidate.name,
    }
    atomic_write_json(host.paths.state / "compose/previous.json", {"services": {}})
    atomic_write_json(
        host.paths.state / "updater-journal.json",
        _legacy_image_journal(tag, candidate.name, phase),
    )

    result = cleanup(host.paths, runner)

    assert result["deleted_tags"] == int(removed)
    assert (exact_tag not in runner.images) is removed
    assert (host.paths.state / "image-owned" / f"{tag}.json").exists() is not removed


def test_failed_rollback_receipt_does_not_authorize_unverified_tag_removal(host):
    from robopark_host.image_retention import cleanup, reserve
    from robopark_host.state import atomic_write_json

    tag = str(uuid4())
    version = "2.0.0"
    candidate = host.paths.releases / f"{version}-{tag}"
    candidate.mkdir()
    reserve(host.paths, candidate, tag)
    image = "sha256:" + "3" * 64
    exact_tag = "robopark-api:" + tag
    runner = Images({exact_tag: image})
    runner.labels[image] = {
        "io.robopark.ota.operation-id": tag,
        "io.robopark.ota.release": candidate.name,
    }
    atomic_write_json(
        host.paths.state / "ota-update-receipts" / f"{tag}.json",
        {
            "operation_id": tag,
            "version": version,
            "sha256": "a" * 64,
            "phase": "failed",
            "started_at": "2026-09-27T09:00:00+00:00",
            "finished_at": "2026-09-27T09:01:00+00:00",
            "error": "ota_rollback_failed",
        },
    )

    result = cleanup(host.paths, runner)

    assert result == {"blocked": False, "deleted_tags": 0, "pending": True}
    assert runner.removed == []
    assert (host.paths.state / "image-owned" / f"{tag}.json").exists()


def test_bot_image_retention_keeps_current_and_rollback_exact_tags(host):
    from robopark_host.image_retention import cleanup, record
    from robopark_host.rollback import atomic_symlink
    from robopark_host.state import atomic_write_json

    inventory = {}
    releases = []
    tags = []
    for version in ("2.0.0", "3.0.0", "4.0.0"):
        release = host.paths.releases / version
        release.mkdir()
        tag = str(uuid4())
        digest = "sha256:" + hashlib.sha256((version + "-bot").encode()).hexdigest()
        inventory[f"robopark-bot:{tag}"] = digest
        record(
            host.paths,
            release,
            tag,
            {
                "services": {
                    "api": {
                        "image": "sha256:"
                        + hashlib.sha256((version + "-api").encode()).hexdigest()
                    },
                    "web": {
                        "image": "sha256:"
                        + hashlib.sha256((version + "-web").encode()).hexdigest()
                    },
                    "bot": {"image": digest},
                }
            },
        )
        atomic_write_json(
            host.paths.state / "successful-releases" / f"{version}.json",
            {"successful": True},
        )
        releases.append(release)
        tags.append(tag)
    atomic_symlink(releases[-2], host.paths.previous)
    atomic_symlink(releases[-1], host.paths.current)
    runner = Images(inventory)

    result = cleanup(host.paths, runner)

    assert result["blocked"] is False
    assert f"robopark-bot:{tags[0]}" in runner.removed
    assert f"robopark-bot:{tags[1]}" not in runner.removed
    assert f"robopark-bot:{tags[2]}" not in runner.removed
