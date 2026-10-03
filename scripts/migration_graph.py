"""Deterministic upgrade planning before any host mutation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from robopark_version import ReleaseVersion


@dataclass(frozen=True, slots=True)
class UpgradePlan:
    releases: tuple[str, ...]
    reversible: bool
    recovery: Literal["rollback", "snapshot"]


@dataclass(frozen=True, slots=True)
class MigrationPolicy:
    target_head: str
    known_heads: frozenset[str]
    bridge_before: ReleaseVersion
    bridge_version: ReleaseVersion
    reversible: bool
    recovery: Literal["rollback", "snapshot"]

    @classmethod
    def from_file(cls, path: Path) -> MigrationPolicy:
        value = json.loads(path.read_text())
        expected = {
            "schema",
            "target_head",
            "known_heads",
            "bridge_before",
            "bridge_version",
            "reversible",
            "recovery",
        }
        try:
            if (
                not isinstance(value, dict)
                or set(value) != expected
                or value["schema"] != 1
                or not isinstance(value["target_head"], str)
                or not isinstance(value["known_heads"], list)
                or not all(
                    isinstance(item, str) and item for item in value["known_heads"]
                )
                or len(value["known_heads"]) != len(set(value["known_heads"]))
                or type(value["reversible"]) is not bool
                or value["recovery"] not in {"rollback", "snapshot"}
                or (value["reversible"] and value["recovery"] != "rollback")
                or (not value["reversible"] and value["recovery"] != "snapshot")
            ):
                raise ValueError
            bridge_before = ReleaseVersion.parse(value["bridge_before"])
            bridge_version = ReleaseVersion.parse(value["bridge_version"])
            if bridge_version.precedence > bridge_before.precedence:
                raise ValueError
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid_migration_policy") from error
        return cls(
            value["target_head"],
            frozenset(value["known_heads"]),
            bridge_before,
            bridge_version,
            value["reversible"],
            value["recovery"],
        )


def plan_upgrade(
    current_version: str,
    current_head: str,
    target_manifest: dict,
    policy: MigrationPolicy,
) -> UpgradePlan:
    try:
        current = ReleaseVersion.parse(current_version)
        target = ReleaseVersion.parse(target_manifest["app_version"])
        if (
            current_head not in policy.known_heads
            or target_manifest["migration_head"] != policy.target_head
            or target.precedence <= current.precedence
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("migration_incompatible") from error
    releases: tuple[str, ...] = (target.raw,)
    if current.precedence < policy.bridge_before.precedence:
        releases = (policy.bridge_version.raw, target.raw)
    return UpgradePlan(releases, policy.reversible, policy.recovery)
