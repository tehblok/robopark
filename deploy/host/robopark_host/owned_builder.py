"""Keep Robopark builds and cache cleanup on one private Buildx builder."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from uuid import uuid4

from .release import ReleaseError, unique_object
from .state import atomic_write_json

_NAME = re.compile(r"robopark-buildkit-[a-f0-9]{32}")
_RECEIPT = "buildkit-builder.json"
_OWNER_ENV = "ROBOPARK_BUILDER_OWNER"


def owned_builder_name(paths) -> str | None:
    path = paths.state / _RECEIPT
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError("builder_ownership_invalid") from exc
    try:
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.geteuid()
                or info.st_size > 1024
            ):
                raise ValueError("builder_ownership_invalid")
            value = json.loads(stream.read(1025), object_pairs_hook=unique_object)
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("builder_ownership_invalid") from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "name", "driver"}
        or value["schema"] != 1
        or type(value["schema"]) is not int
        or not isinstance(value["name"], str)
        or _NAME.fullmatch(value["name"]) is None
        or value["driver"] != "docker-container"
    ):
        raise ValueError("builder_ownership_invalid")
    return value["name"]


def inspect_owned_builder(paths, run, *, bootstrap: bool = False) -> str:
    """Reject a replaced Buildx instance before any cache mutation."""
    name = owned_builder_name(paths)
    if name is None:
        raise ValueError("builder_ownership_invalid")
    output = run(["docker", "buildx", "inspect", *(["--bootstrap"] if bootstrap else []), name])
    if not isinstance(output, str) or len(output) > 65536:
        raise ValueError("builder_identity_invalid")
    names = re.findall(r"^Name:[ \t]*(\S+)[ \t]*$", output, re.MULTILINE)
    drivers = re.findall(r"^Driver:[ \t]*(\S+)[ \t]*$", output, re.MULTILINE)
    if names not in ([name], [name, name + "0"]) or drivers != ["docker-container"]:
        raise ValueError("builder_identity_invalid")
    container = "buildx_buildkit_" + name + "0"
    environment = run([
        "docker", "inspect", "--type", "container", "--format",
        "{{json .Config.Env}}", container,
    ])
    if not isinstance(environment, str) or len(environment) > 65536:
        raise ValueError("builder_identity_invalid")
    try:
        variables = json.loads(environment)
    except (ValueError, TypeError) as exc:
        raise ValueError("builder_identity_invalid") from exc
    if (not isinstance(variables, list)
            or not all(isinstance(item, str) for item in variables)
            or variables.count(f"{_OWNER_ENV}={name}") != 1):
        raise ValueError("builder_identity_invalid")
    return name


def ensure_owned_builder(paths, runner) -> str:
    """Create once under the host lock; never switch Docker's global default."""
    name = owned_builder_name(paths)
    if name is None:
        name = f"robopark-buildkit-{uuid4().hex}"
        # The durable identity precedes Docker creation so an interrupted
        # create can be resumed without inventing another cache volume.
        atomic_write_json(
            paths.state / _RECEIPT,
            {"schema": 1, "name": name, "driver": "docker-container"},
        )
    inspect = ["docker", "buildx", "inspect", name]

    def run(argv, *, timeout: int, capture: bool = False):
        if callable(runner):
            return runner(argv)
        return runner.run(argv, timeout=timeout, capture=capture)

    # Absence is expected on first use. Do not claim the OTA failure log for
    # this handled probe; create/bootstrap/build failures must retain it.
    probe = getattr(runner, "run_cleanup", None)
    if callable(probe):
        exists = probe(inspect, timeout=30)
    else:
        try:
            run(inspect, timeout=30, capture=True)
            exists = True
        except (OSError, ReleaseError, subprocess.CalledProcessError):
            exists = False
    if not exists:
        run(
            [
                "docker", "buildx", "create", "--name", name,
                "--driver", "docker-container", "--driver-opt", "default-load=true",
                "--driver-opt", f"env.{_OWNER_ENV}={name}",
            ],
            timeout=90,
        )

    def verified_run(argv):
        raw = run(argv, timeout=90 if "--bootstrap" in argv else 30, capture=True)
        if isinstance(raw, bytes):
            return raw.decode("utf-8", "replace")
        return raw

    # A matching Buildx name/driver is insufficient: a replaced container may
    # use another project's cache. Bootstrap it so the owner marker is inspectable.
    return inspect_owned_builder(paths, verified_run, bootstrap=True)
