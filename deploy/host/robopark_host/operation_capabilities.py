"""Bounded, boot-scoped discovery of the actual injected typed adapter."""

import hashlib
import json
import os
from datetime import UTC, datetime
from uuid import UUID

from .state import atomic_write_json

CAPABILITY_TTL_SECONDS = 300


def operation_capabilities(effects):
    from .commands import OperationKind

    supported = getattr(effects, "supported_kinds", frozenset())
    reasons = getattr(effects, "unavailable_reasons", {})
    if not isinstance(supported, frozenset) or not supported <= set(OperationKind):
        supported = frozenset()
    if not isinstance(reasons, dict):
        reasons = {}
    return {
        kind.value: {
            "available": kind in supported,
            "unavailable_reason": None if kind in supported else (
                "context_unavailable"
                if reasons.get(kind) == "context_unavailable"
                else "capability_unavailable"
            ),
        }
        for kind in OperationKind
    }


def _boot_id(paths):
    try:
        descriptor = os.open(
            paths.root / "proc/sys/kernel/random/boot_id",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        with os.fdopen(descriptor, "rb") as stream:
            value = stream.read(38).decode("ascii").strip()
        return value if str(UUID(value)) == value else None
    except (OSError, ValueError, UnicodeError):
        return None


def capability_revision(boot_id, operations):
    return hashlib.sha256(json.dumps(
        {"boot_id": boot_id, "operations": operations}, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def current_capability_revision(paths, effects):
    boot_id = _boot_id(paths)
    if boot_id is None:
        return None
    return capability_revision(boot_id, operation_capabilities(effects))


def publish_operation_capabilities(paths, effects):
    """Publish declarations only; never probe or execute an effect for discovery."""
    value = {
        "schema": 1,
        "boot_id": _boot_id(paths),
        "generated_at": datetime.now(UTC).isoformat(),
        "valid_for_seconds": CAPABILITY_TTL_SECONDS,
        "operations": operation_capabilities(effects),
    }
    directory = paths.ops / "public"
    directory.mkdir(parents=True, exist_ok=True, mode=0o755)
    directory.chmod(0o755)
    atomic_write_json(directory / "operation-capabilities.json", value, mode=0o644)
    return value
