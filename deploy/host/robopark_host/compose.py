"""Stable Docker Compose invocation and safe service-state parsing."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .paths import HostPaths

PROJECT_NAME = "robopark"
EXPECTED_SERVICES = frozenset({"db", "api", "web"})


def compose_command(paths: HostPaths, arguments: Sequence[str]) -> list[str]:
    """Address exactly the compose file belonging to the active immutable release."""

    compose_file = paths.state / "current-compose.json"
    return [
        "docker",
        "compose",
        "--project-name",
        PROJECT_NAME,
        "--project-directory",
        str(compose_file.parent),
        "--file",
        str(compose_file),
        *arguments,
    ]


def parse_compose_services(output: str) -> list[dict[str, Any]] | None:
    """Accept Docker Compose's array, object, and JSON-lines output forms."""

    text = output.strip()
    if not text:
        return []
    try:
        decoded = json.loads(text)
    except ValueError:
        entries: list[Any] = []
        try:
            for line in text.splitlines():
                if line.strip():
                    entries.append(json.loads(line))
        except ValueError:
            return None
    else:
        entries = decoded if isinstance(decoded, list) else [decoded]
    if not all(isinstance(item, Mapping) for item in entries):
        return None
    return [dict(item) for item in entries]


def safe_compose_services(services: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return only operational service fields, never commands or environment data."""

    output = []
    for service in services:
        if service.get("Service") not in ("db", "api", "worker", "web", "ops-agent"):
            continue
        item = {"Service": service["Service"]}
        if service.get("State") in (
            "running",
            "exited",
            "created",
            "restarting",
            "paused",
            "dead",
            "removing",
        ):
            item["State"] = service["State"]
        if service.get("Health") in ("", "healthy", "unhealthy", "starting"):
            item["Health"] = service["Health"]
        for field in ("ExitCode", "RestartCount"):
            value = service.get(field)
            if type(value) is int and 0 <= value < 2**31:
                item[field] = value
        output.append(item)
    return output
