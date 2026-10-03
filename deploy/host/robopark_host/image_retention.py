"""Bounded cleanup of exact owned image tags; never broad Docker system/image/container/network/volume prune."""

import json
import os
import re
import shutil
import stat
import time
from contextlib import ExitStack, suppress

from .operational_state import read_object
from .owned_builder import inspect_owned_builder
from .retention import UUID, StorageBudget, _directory
from .state import atomic_write_json, host_operation

TAG = rf"(?:{UUID}|release-[a-f0-9]{{64}})"
DIGEST = r"sha256:[a-f0-9]{64}"
# Shared image IDs can leave harmless old tags for safety; keep enough
# ownership receipts for years of updates without a 256-update hard stop.
MAX_RECORDS = 4096
MAX_COMMANDS = 32
BUILDER_CACHE_TIMEOUT = 30
MAINTENANCE_TIMEOUT = 30
OPERATION_LABEL = "io.robopark.ota.operation-id"
RELEASE_LABEL = "io.robopark.ota.release"
OWNED_IMAGE_SERVICES = ("api", "web", "bot")


class ImageCleanupPartialError(ValueError):
    """The exact manual plan was consumed; Docker may have removed some tags."""

    def __init__(self, deleted, uncertain_target):
        super().__init__("image_cleanup_partial")
        self.deleted = deleted
        self.uncertain_target = uncertain_target


class _CleanupBudget:
    """One bounded Docker budget shared by image and builder-cache cleanup."""

    def __init__(self, timeout):
        self.calls = 0
        self.deadline = time.monotonic() + timeout

    def timeout(self, maximum):
        if self.calls >= MAX_COMMANDS:
            raise TimeoutError()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        self.calls += 1
        return min(maximum, remaining)


def _docker_run(runner, budget, argv):
    raw = runner.run(argv, timeout=budget.timeout(5), capture=True)
    if not isinstance(raw, (str, bytes)) or len(raw) > 2 * 1024**2:
        raise ValueError("invalid_docker_response")
    return raw.decode("utf8") if isinstance(raw, bytes) else raw


def _image_inventory(run):
    inventory = {}
    for line in run(
        ["docker", "image", "ls", "--no-trunc", "--format", "{{.Repository}}:{{.Tag}} {{.ID}}"]
    ).splitlines():
        parts = line.split()
        if len(parts) != 2 or re.fullmatch(DIGEST, parts[1]) is None:
            raise ValueError("invalid_docker_response")
        if parts[0] in inventory and inventory[parts[0]] != parts[1]:
            raise ValueError("invalid_docker_response")
        inventory[parts[0]] = parts[1]
    return inventory


def _docker_inventory(run):
    inventory = _image_inventory(run)
    containers = run(["docker", "ps", "--all", "--quiet", "--no-trunc"]).splitlines()
    if len(containers) > 256 or any(
        re.fullmatch(r"[a-f0-9]{64}", identity) is None for identity in containers
    ):
        raise ValueError("invalid_docker_response")
    running_images = set()
    if containers:
        rows = run(["docker", "inspect", "--format", "{{.Image}}", *containers]).splitlines()
        if len(rows) != len(containers) or any(
            re.fullmatch(DIGEST, row) is None for row in rows
        ):
            raise ValueError("invalid_docker_response")
        running_images.update(rows)
    return inventory, running_images


def _records(paths):
    result = []
    with ExitStack() as stack:
        try:
            parent = stack.enter_context(_directory(paths, paths.state / "image-owned"))
        except FileNotFoundError:
            return result
        with os.scandir(parent) as listing:
            seen = 0
            for entry in listing:
                seen += 1
                if seen > MAX_RECORDS:
                    raise ValueError("image_retention_required")
                if re.fullmatch(r"\." + TAG + r"\.json\.[A-Za-z0-9_]+", entry.name):
                    continue  # interrupted atomic write, never trusted as ownership
                if re.fullmatch(TAG + r"\.json", entry.name) is None:
                    raise ValueError("invalid_image_ownership")
                fd = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                with os.fdopen(fd, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or stat.S_IMODE(info.st_mode) != 0o600
                        or info.st_uid != os.geteuid()
                        or info.st_nlink != 1
                        or info.st_size > 4096
                    ):
                        raise ValueError("invalid_image_ownership")
                    from .release import unique_object

                    value = json.loads(stream.read(4097), object_pairs_hook=unique_object)
                if (
                    not isinstance(value, dict)
                    or set(value) != {"schema", "tag", "release", "images"}
                    or type(value["schema"]) is not int
                    or value["schema"] != 1
                    or value["tag"] != entry.name[:-5]
                    or not isinstance(value["release"], str)
                    or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,200}", value["release"]) is None
                    or (
                        value["images"] is not None
                        and (
                            not isinstance(value["images"], dict)
                            or not set(value["images"]) <= set(OWNED_IMAGE_SERVICES)
                            or any(
                                not isinstance(image, str)
                                or re.fullmatch(DIGEST, image) is None
                                for image in value["images"].values()
                            )
                        )
                    )
                ):
                    raise ValueError("invalid_image_ownership")
                result.append(value)
    return result


def require_record_capacity(paths):
    if len(_records(paths)) >= MAX_RECORDS:
        raise ValueError("image_retention_required")


def reserve(paths, release, tag):
    """Durably claim the exact production tags before Docker can create them."""
    if re.fullmatch(TAG, tag) is None:
        raise ValueError("invalid_image_tag")
    atomic_write_json(
        paths.state / "image-owned" / (tag + ".json"),
        {"schema": 1, "release": release.name, "tag": tag, "images": None},
    )


def label_build(build, release, tag):
    """Mark the resulting image so a crashed build can be reconciled later."""
    labels = build.get("labels", {})
    if isinstance(labels, list):
        labels = dict(label.split("=", 1) for label in labels)
    if not isinstance(labels, dict):
        raise TypeError("invalid_build_labels")
    build["labels"] = {**labels, OPERATION_LABEL: tag, RELEASE_LABEL: release}


def _reconcile_failed_reservation(paths, item, inventory, run):
    """Record exact labeled image IDs only after a durable failed OTA receipt."""
    from .ota_update import OtaUpdateReceipt
    from .updater import _load_journal

    receipt_path = paths.state / "ota-update-receipts" / (item["tag"] + ".json")
    if receipt_path.exists() or receipt_path.is_symlink():
        try:
            parsed = OtaUpdateReceipt.from_dict(read_object(receipt_path))
            terminal = (
                parsed.operation_id == item["tag"]
                and item["release"] == f"{parsed.version}-{parsed.operation_id}"
                and parsed.phase in {"failed", "rolled_back"}
                and parsed.error != "ota_rollback_failed"
            )
        except (TypeError, KeyError, AttributeError):
            terminal = False
    else:
        legacy = _load_journal(paths)
        terminal = bool(
            legacy
            and legacy["job_id"] == item["tag"]
            and legacy["candidate"] == item["release"]
            and legacy["phase"] in {"failed", "rolled_back"}
            and legacy["error"] != "manual_recovery_required"
        )
    if not terminal:
        return False
    found = {}
    for service in OWNED_IMAGE_SERVICES:
        tag = "robopark-" + service + ":" + item["tag"]
        digest = inventory.get(tag)
        if digest is None:
            continue
        labels = json.loads(run([
            "docker", "image", "inspect", "--format", "{{json .Config.Labels}}", tag,
        ]))
        if not isinstance(labels, dict) or labels.get(OPERATION_LABEL) != item["tag"] or labels.get(RELEASE_LABEL) != item["release"]:
            return False
        if run(["docker", "image", "inspect", "--format", "{{.Id}}", tag]).strip() != digest:
            raise ValueError("image_tag_changed")
        found[service] = digest
    item["images"] = found
    atomic_write_json(paths.state / "image-owned" / (item["tag"] + ".json"), item)
    return True


def record(paths, release, tag, document):
    if re.fullmatch(TAG, tag) is None:
        raise ValueError("invalid_image_tag")
    value = {
        "schema": 1,
        "release": release.name,
        "tag": tag,
        "images": {
            key: document["services"][key]["image"]
            for key in OWNED_IMAGE_SERVICES
            if key in document["services"]
        },
    }
    atomic_write_json(paths.state / "image-owned" / (tag + ".json"), value)


def _protected(paths, records):
    from .ota_update import OtaUpdateEngine, OtaUpdateRequest
    from .updater import _load_journal, _retained_successful_releases

    try:
        keep = _retained_successful_releases(paths)
    except Exception as exc:
        raise ValueError("invalid_release_link") from exc

    journal = _load_journal(paths)
    if journal and journal["phase"] not in {"succeeded", "rolled_back", "failed"}:
        keep.update((journal["previous"], journal["candidate"]))
    hash_journal_path = paths.state / "ota-update-journal.json"
    if hash_journal_path.is_symlink():
        raise ValueError("invalid_ota_update_journal")
    try:
        hash_journal = OtaUpdateEngine(paths, None)._read_journal()
    except RuntimeError as exc:
        raise ValueError("invalid_ota_update_journal") from exc
    if hash_journal and (
        hash_journal["phase"] not in {"published", "rolled_back", "failed"}
        or hash_journal["error"] == "ota_rollback_failed"
    ):
        request = OtaUpdateRequest.from_dict(hash_journal["request"])
        keep.add(f"{request.version}-{request.operation_id}")
    retained = [item for item in records if item["release"] in keep]
    images = {
        image
        for item in retained
        if item["images"] is not None
        for image in item["images"].values()
    }
    tags = {
        "robopark-" + service + ":" + item["tag"]
        for item in retained
        if item["images"] is not None
        for service in item["images"]
    }
    # Runtime configs can protect images from an installation predating receipts.
    configs = [paths.state / "current-compose.json"]
    if journal:
        configs.append(paths.state / journal["previous_config"])
    for path in configs:
        document = read_object(path.resolve(strict=True))
        services = document.get("services")
        if not isinstance(services, dict):
            raise TypeError("invalid_runtime_state")
        for service in services.values():
            image = service.get("image") if isinstance(service, dict) else None
            if isinstance(image, str) and re.fullmatch(DIGEST, image):
                images.add(image)
    return keep, images, tags


def preview_owned_images(paths, runner, *, budget=None):
    """Inspect exact obsolete owned tags without changing Docker or host receipts.

    Reported image sizes include shared layers and are not a freed-byte promise.
    """
    result = {"blocked": False, "planned": [], "total_reported_bytes": 0,
              "unverified_tags": 0}
    budget = budget or _CleanupBudget(25)

    def run(argv):
        return _docker_run(runner, budget, argv)

    try:
        records = _records(paths)
        if not records:
            return result
        keep, protected, retained_tags = _protected(paths, records)
        inventory, running_images = _docker_inventory(run)
        protected.update(running_images)
        sizes = {}
        for item in sorted(records, key=lambda value: value["tag"]):
            if item["release"] in keep:
                continue
            for service in OWNED_IMAGE_SERVICES:
                tag = "robopark-" + service + ":" + item["tag"]
                current = inventory.get(tag)
                if current is None:
                    continue
                owned = item["images"]
                if owned is None:
                    result["unverified_tags"] += 1
                    continue
                expected = owned.get(service)
                if expected is None:
                    result["unverified_tags"] += 1
                    continue
                if current != expected:
                    raise ValueError("image_tag_changed")
                if any(name != tag and image == expected for name, image in inventory.items()):
                    result["unverified_tags"] += 1
                    continue  # removing by image ID must not affect another tag
                alias = any(inventory.get(name) == expected for name in retained_tags)
                if expected in protected and not alias:
                    continue
                inspected = run([
                    "docker", "image", "inspect", "--format", "{{.Id}} {{.Size}}", tag,
                ]).strip().split()
                if (
                    len(inspected) != 2 or inspected[0] != expected
                    or re.fullmatch(r"[0-9]{1,19}", inspected[1]) is None
                ):
                    raise ValueError("image_tag_changed")
                size = int(inspected[1])
                if size >= 2**63:
                    raise ValueError("invalid_docker_response")
                result["planned"].append({
                    "tag": tag, "image_id": expected, "reported_bytes": size,
                })
                sizes[expected] = size
        result["total_reported_bytes"] = sum(sizes.values())
        if result["total_reported_bytes"] >= 2**63:
            raise ValueError("invalid_docker_response")
    except (TimeoutError, OSError, ValueError, TypeError, RecursionError, UnicodeError):
        result["blocked"] = True
        result["planned"] = []
        result["total_reported_bytes"] = 0
    return result


def remove_previewed_images(runner, planned):
    """Remove only the already confirmed tags, non-force, rechecking each digest."""
    if not isinstance(planned, list) or not planned or len(planned) > MAX_RECORDS:
        raise ValueError("invalid_image_plan")
    tags = set()
    for item in planned:
        if (
            not isinstance(item, dict)
            or set(item) != {"tag", "image_id", "reported_bytes"}
            or not isinstance(item["tag"], str)
            or re.fullmatch(r"robopark-(?:api|web|bot):" + TAG, item["tag"]) is None
            or item["tag"] in tags
            or not isinstance(item["image_id"], str)
            or re.fullmatch(DIGEST, item["image_id"]) is None
            or type(item["reported_bytes"]) is not int
            or not 0 <= item["reported_bytes"] < 2**63
        ):
            raise ValueError("invalid_image_plan")
        tags.add(item["tag"])
    budget = _CleanupBudget(25)
    deleted = []
    for item in planned:
        tag = item["tag"]
        expected = item["image_id"]
        try:
            inventory = _image_inventory(lambda argv: _docker_run(runner, budget, argv))
            if inventory.get(tag) != expected or any(
                name != tag and image == expected for name, image in inventory.items()
            ):
                raise ValueError("image_tag_changed")
            observed = _docker_run(
                runner, budget, ["docker", "image", "inspect", "--format", "{{.Id}}", tag]
            ).strip()
            if observed != expected:
                raise ValueError("image_tag_changed")
            # A tag can be reassigned between inspect and rm. Address the
            # authenticated image ID, so a replacement tag cannot be removed.
            _docker_run(runner, budget, ["docker", "image", "rm", "--no-prune", expected])
            if tag in _image_inventory(lambda argv: _docker_run(runner, budget, argv)):
                raise ValueError("image_tag_changed")
            deleted.append({"tag": tag, "reported_bytes": item["reported_bytes"]})
        except Exception as exc:
            raise ImageCleanupPartialError(
                deleted, {"tag": tag, "reported_bytes": item["reported_bytes"]}
            ) from exc
    return {"deleted": deleted, "deleted_count": len(deleted), "uncertain_target": None}


def cleanup(paths, runner, *, budget=None):
    """Caller owns host.lock. Errors become bounded diagnostics, never rollback."""
    result = {"blocked": False, "deleted_tags": 0, "pending": False}
    budget = budget or _CleanupBudget(25)

    def run(argv):
        return _docker_run(runner, budget, argv)

    try:
        records = _records(paths)
        if not records:
            return result
        keep, protected, retained_tags = _protected(paths, records)
        inventory, running_images = _docker_inventory(run)
        protected.update(running_images)
        for item in records:
            if item["release"] in keep:
                continue
            complete = True
            owned = item["images"]
            if (owned is None and item["release"] not in keep
                    and _reconcile_failed_reservation(paths, item, inventory, run)):
                owned = item["images"]
            for service in OWNED_IMAGE_SERVICES:
                tag = "robopark-" + service + ":" + item["tag"]
                current = inventory.get(tag)
                if current is None:
                    continue  # earlier interrupted cleanup already removed this tag
                if owned is None:
                    # A reservation without its recorded image ID cannot be
                    # removed safely: tags can be reassigned between commands.
                    complete = False
                    continue
                expected = owned.get(service)
                if expected is None:
                    complete = False
                    continue
                if current != expected:
                    raise ValueError("image_tag_changed")
                if any(name != tag and image == expected for name, image in inventory.items()):
                    complete = False
                    continue  # exact owned tag shares its image with another tag
                alias = any(
                    inventory.get(name) == expected for name in retained_tags
                )
                if expected in protected and not alias:
                    complete = False
                    continue
                # Recheck the exact tag immediately before the non-force removal.
                observed = run(
                    ["docker", "image", "inspect", "--format", "{{.Id}}", tag]
                ).strip()
                if observed != expected:
                    raise ValueError("image_tag_changed")
                run(["docker", "image", "rm", "--no-prune", expected])
                inventory = _image_inventory(run)
                if tag in inventory:
                    raise ValueError("image_tag_changed")
                result["deleted_tags"] += 1
            if complete:
                with _directory(paths, paths.state / "image-owned") as parent:
                    os.unlink(item["tag"] + ".json", dir_fd=parent)
                    os.fsync(parent)
            else:
                result["pending"] = True
    except TimeoutError:
        result["pending"] = True
    except (OSError, ValueError, TypeError, RecursionError):
        result["blocked"] = True
    finally:
        with suppress(OSError):
            atomic_write_json(paths.state / "image-retention.json", result)
    return result


def cleanup_builder_cache(paths, runner, *, budget=None, force=False):
    """Keep unused BuildKit cache within a fixed budget after builds or under pressure."""
    # Docker parses suffixes like GB as binary units. Use exact bytes for both
    # the requested limit and the measured postcondition.
    max_used_bytes = 2_000_000_000
    result = {"attempted": False, "blocked": False}
    try:
        usage = shutil.disk_usage(paths.var)
        storage = StorageBudget(
            partition_bytes=getattr(usage, "total", 0), free_bytes=usage.free
        )
        if not force and storage.bytes_to_reclaim == 0:
            return result
        budget = budget or _CleanupBudget(MAINTENANCE_TIMEOUT)
        builder = inspect_owned_builder(
            paths, lambda argv: _docker_run(runner, budget, argv)
        )
        result["attempted"] = True
        timeout = budget.timeout(BUILDER_CACHE_TIMEOUT)
        runner.run(
            [
                "docker",
                "buildx",
                "prune",
                "--builder",
                builder,
                "-f",
                "--all",
                "--max-used-space",
                str(max_used_bytes),
            ],
            timeout=timeout,
        )
        from .storage_inventory import parse_owned_builder_du

        observed = _docker_run(
            runner, budget,
            ["docker", "buildx", "du", "--builder", builder, "--format=json"],
        )
        measured = parse_owned_builder_du(observed)
        if measured is None or measured["reported_bytes"] > max_used_bytes:
            result["blocked"] = True
    except Exception:  # noqa: BLE001 -- any Docker failure must block cleanup
        result["blocked"] = True
    finally:
        with suppress(OSError):
            atomic_write_json(paths.state / "builder-cache-retention.json", result)
        if result["attempted"] or result["blocked"]:
            from .health_projection import update_public_health

            with suppress(OSError):
                update_public_health(
                    paths.var / "api-ops/host-health.json",
                    builder_cache_budget={
                        "attempted": result["attempted"],
                        "blocked": result["blocked"],
                        "checked_at": time.time(),
                    },
                )
    return result


def maintenance(paths, runner, *, enforce_builder_budget=False):
    """Run both image stages under one command and elapsed-time budget."""
    budget = _CleanupBudget(MAINTENANCE_TIMEOUT)
    builder_cache = None
    if enforce_builder_budget:
        builder_cache = cleanup_builder_cache(paths, runner, budget=budget, force=True)
    result = cleanup(paths, runner, budget=budget)
    result["builder_cache"] = builder_cache or cleanup_builder_cache(
        paths, runner, budget=budget
    )
    with suppress(OSError):
        atomic_write_json(paths.state / "image-retention.json", result)
    return result


def scheduled(paths, runner):
    from .state import HostBusy

    try:
        with host_operation(paths):
            return maintenance(paths, runner)
    except HostBusy:
        return {"busy": True}
