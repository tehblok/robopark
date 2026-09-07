"""Bounded deletion of exact root-recorded production image tags, never Docker prune."""

import json
import os
import re
import stat
import time
from contextlib import ExitStack, suppress

from .operational_state import read_object
from .retention import UUID, _directory
from .state import atomic_write_json, host_operation

TAG = rf"(?:{UUID}|release-[a-f0-9]{{64}})"
DIGEST = r"sha256:[a-f0-9]{64}"
MAX_RECORDS = 256
MAX_COMMANDS = 32


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
                            or set(value["images"]) != {"api", "web"}
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


def record(paths, release, tag, document):
    if re.fullmatch(TAG, tag) is None:
        raise ValueError("invalid_image_tag")
    value = {
        "schema": 1,
        "release": release.name,
        "tag": tag,
        "images": {key: document["services"][key]["image"] for key in ("api", "web")},
    }
    atomic_write_json(paths.state / "image-owned" / (tag + ".json"), value)


def _protected(paths, records):
    keep = set()
    for link in (paths.current, paths.previous):
        if link == paths.previous and not link.exists() and not link.is_symlink():
            continue
        target = link.resolve(strict=True)
        if not link.is_symlink() or target.parent != paths.releases.resolve():
            raise ValueError("invalid_release_link")
        keep.add(target.name)
    from .updater import _load_journal

    journal = _load_journal(paths)
    if journal and journal["phase"] not in {"succeeded", "rolled_back", "failed"}:
        keep.update((journal["previous"], journal["candidate"]))
    retained = [item for item in records if item["release"] in keep]
    images = {
        image
        for item in retained
        if item["images"] is not None
        for image in item["images"].values()
    }
    tags = {
        "robopark-" + service + ":" + item["tag"] for item in retained for service in ("api", "web")
    }
    # Runtime configs can protect images from an installation predating receipts.
    configs = [paths.state / "current-compose.json"]
    if journal:
        configs.append(paths.state / journal["previous_config"])
    for path in configs:
        document = read_object(path.resolve(strict=True))
        services = document.get("services")
        if not isinstance(services, dict):
            raise ValueError("invalid_runtime_state")
        for service in services.values():
            image = service.get("image") if isinstance(service, dict) else None
            if isinstance(image, str) and re.fullmatch(DIGEST, image):
                images.add(image)
    return keep, images, tags


def cleanup(paths, runner):
    """Caller owns host.lock. Errors become bounded diagnostics, never rollback."""
    result = {"blocked": False, "deleted_tags": 0, "pending": False}
    deadline = time.monotonic() + 25
    calls = 0

    def run(argv):
        nonlocal calls
        calls += 1
        left = deadline - time.monotonic()
        if calls > MAX_COMMANDS or left <= 0:
            raise TimeoutError()
        raw = runner.run(argv, timeout=min(5, left), capture=True)
        if not isinstance(raw, (str, bytes)) or len(raw) > 2 * 1024**2:
            raise ValueError("invalid_docker_response")
        return raw.decode("utf8") if isinstance(raw, bytes) else raw

    try:
        records = _records(paths)
        if not records:
            return result
        keep, protected, retained_tags = _protected(paths, records)
        inventory = {}
        for line in run(
            ["docker", "image", "ls", "--no-trunc", "--format", "{{.Repository}}:{{.Tag}} {{.ID}}"]
        ).splitlines():
            parts = line.split()
            if len(parts) != 2 or re.fullmatch(DIGEST, parts[1]) is None:
                raise ValueError("invalid_docker_response")
            inventory[parts[0]] = parts[1]
        containers = run(["docker", "ps", "--all", "--quiet", "--no-trunc"]).splitlines()
        if len(containers) > 256 or any(
            re.fullmatch(r"[a-f0-9]{64}", identity) is None for identity in containers
        ):
            raise ValueError("invalid_docker_response")
        if containers:
            rows = run(["docker", "inspect", "--format", "{{.Image}}", *containers]).splitlines()
            if len(rows) != len(containers) or any(
                re.fullmatch(DIGEST, row) is None for row in rows
            ):
                raise ValueError("invalid_docker_response")
            protected.update(rows)
        for item in records:
            if item["release"] in keep:
                continue
            complete = True
            owned = item["images"]
            for service in ("api", "web"):
                tag = "robopark-" + service + ":" + item["tag"]
                current = inventory.get(tag)
                if current is None:
                    continue  # earlier interrupted cleanup already removed this tag
                expected = owned.get(service) if owned is not None else None
                if expected is not None and current != expected:
                    raise ValueError("image_tag_changed")
                alias = expected is not None and any(
                    inventory.get(name) == expected for name in retained_tags
                )
                if expected is not None and expected in protected and not alias:
                    complete = False
                    continue
                # Recheck the exact tag immediately before the non-force removal.
                observed = run(
                    ["docker", "image", "inspect", "--format", "{{.Id}}", tag]
                ).strip()
                if re.fullmatch(DIGEST, observed) is None or (
                    expected is not None and observed != expected
                ):
                    raise ValueError("image_tag_changed")
                run(["docker", "image", "rm", tag])
                inventory.pop(tag)
                result["deleted_tags"] += 1
            if complete:
                with _directory(paths, paths.state / "image-owned") as parent:
                    os.unlink(item["tag"] + ".json", dir_fd=parent)
                    os.fsync(parent)
            else:
                result["pending"] = True
    except TimeoutError:
        result["pending"] = True
    except (OSError, ValueError, RecursionError):
        result["blocked"] = True
    finally:
        with suppress(OSError):
            atomic_write_json(paths.state / "image-retention.json", result)
    return result


def scheduled(paths, runner):
    from .state import HostBusy

    try:
        with host_operation(paths):
            return cleanup(paths, runner)
    except HostBusy:
        return {"busy": True}
