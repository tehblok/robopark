"""Prune only authenticated host-owned obsolete production images."""

import hashlib

import pytest
from robopark_host.updater import apply_release, reconcile_after_exit
from test_updater import host as host_factory


@pytest.fixture
def host(host_paths):
    return host_factory.__wrapped__(host_paths)


def test_four_versions_remove_old_tags_but_preserve_current_previous_and_foreign(host):
    original = host.runner.run
    images = {}
    removals = []

    def run(argv, **kwargs):
        if argv[:3] == ["docker", "image", "inspect"]:
            tag = str(argv[-1])
            return images.setdefault(
                tag, "sha256:" + hashlib.sha256(tag.encode()).hexdigest()
            ).encode()
        if argv[:3] == ["docker", "image", "ls"]:
            return "\n".join(tag + " " + digest for tag, digest in images.items()).encode()
        if argv[:3] == ["docker", "image", "rm"]:
            removals.extend(argv[3:])
        if argv[:2] == ["docker", "ps"]:
            return b""
        return original(argv, **kwargs)

    host.runner.run = run
    jobs = []
    for version in ["2.0.0", "3.0.0", "4.0.0", "5.0.0"]:
        archive = host.package(
            version,
            meta={"migration_compatibility": {"from_heads": ["old", "new"], "reversible": True}},
        )
        request = host.request(archive)
        jobs.append(request.job_id)
        assert apply_release(request, host.paths, host.runner).error is None
        assert reconcile_after_exit(host.paths, host.runner).state == "current_healthy"
    for job in jobs[:2]:
        assert "robopark-api:" + job in removals
        assert "robopark-web:" + job in removals
    for job in jobs[2:]:
        assert "robopark-api:" + job not in removals
        assert "robopark-web:" + job not in removals
    assert not any(tag.startswith("sha256:") for tag in removals)
    assert all(
        tag.startswith(
            ("robopark-api:", "robopark-web:", "robopark-api-tests:", "robopark-web-tests:")
        )
        for tag in removals
    )


def obsolete_image(host):
    from uuid import uuid4

    from robopark_host.image_retention import record

    tag = str(uuid4())
    document = {
        "services": {
            name: {"image": "sha256:" + digit * 64} for name, digit in [("api", "7"), ("web", "8")]
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

    def run(self, argv, **kwargs):
        assert 0 < kwargs["timeout"] <= 5
        if argv[:3] == ["docker", "image", "ls"]:
            return "\n".join(tag + " " + digest for tag, digest in self.images.items()).encode()
        if argv[:2] == ["docker", "ps"]:
            return b"a" * 64 if self.running else b""
        if argv[:2] == ["docker", "inspect"]:
            return "\n".join(self.running).encode()
        if argv[:3] == ["docker", "image", "inspect"]:
            return self.images[argv[-1]].encode()
        assert argv[:3] == ["docker", "image", "rm"]
        if self.fail:
            raise ValueError("SECRET=must-not-appear")
        self.removed.append(argv[-1])
        self.images.pop(argv[-1])
        return b""


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
    assert not cleanup(host.paths, runner)["blocked"]
    assert not runner.images


def test_scheduled_image_cleanup_respects_host_operation_owner(host):
    from robopark_host.image_retention import scheduled
    from robopark_host.state import exclusive_lock

    _, inventory = obsolete_image(host)
    runner = Images(inventory)
    with exclusive_lock(host.paths.ops / "host.lock"):
        assert scheduled(host.paths, runner)["busy"]
    assert not runner.removed


def test_interrupted_atomic_receipt_temporary_does_not_block_next_cleanup(host):
    from robopark_host.image_retention import cleanup

    tag, inventory = obsolete_image(host)
    (host.paths.state / "image-owned" / ("." + tag + ".json.tmp12345")).write_text("partial")
    assert not cleanup(host.paths, Images(inventory))["blocked"]


def test_failed_candidate_images_are_reclaimed_after_safe_rollback(host):
    original = host.runner.run
    images = {}
    removed = []

    def run(argv, **kwargs):
        if argv[:3] == ["docker", "image", "inspect"]:
            tag = argv[-1]
            return images.setdefault(
                tag, "sha256:" + hashlib.sha256(tag.encode()).hexdigest()
            ).encode()
        if argv[:3] == ["docker", "image", "ls"]:
            return "\n".join(tag + " " + digest for tag, digest in images.items()).encode()
        if argv[:3] == ["docker", "image", "rm"]:
            removed.extend(argv[3:])
        if argv[:2] == ["docker", "ps"]:
            return b""
        return original(argv, **kwargs)

    host.runner.run = run
    request = host.request()
    host.runner.health = False
    assert apply_release(request, host.paths, host.runner).state == "previous_restored"
    assert host.paths.current.resolve().name == "1.0.0"
    assert "robopark-api:" + request.job_id in removed
    assert "robopark-web:" + request.job_id in removed
