"""Release support policy and manifest-v3 validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from robopark_version import BuildIdentity, ReleaseVersion


@dataclass(frozen=True, slots=True)
class ReleaseSupport:
    support_class: str
    support_months: int
    eligible_channels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SupportPolicy:
    stable_support_months: int
    lts_support_months: int
    channels: tuple[str, ...]
    lts_lines: frozenset[str]

    @classmethod
    def from_file(cls, path: Path) -> SupportPolicy:
        value = json.loads(path.read_text())
        if (
            not isinstance(value, dict)
            or set(value)
            != {"schema", "stable_support_months", "lts_support_months", "channels", "lts_lines"}
            or value["schema"] != 1
            or type(value["stable_support_months"]) is not int
            or type(value["lts_support_months"]) is not int
            or not 1 <= value["stable_support_months"] <= value["lts_support_months"] <= 120
            or value["channels"] != ["stable", "rc", "manual"]
            or not isinstance(value["lts_lines"], list)
            or not all(isinstance(item, str) for item in value["lts_lines"])
        ):
            raise ValueError("invalid_support_policy")
        return cls(
            value["stable_support_months"],
            value["lts_support_months"],
            tuple(value["channels"]),
            frozenset(value["lts_lines"]),
        )

    def release(self, version: str) -> ReleaseSupport:
        parsed = ReleaseVersion.parse(version)
        if parsed.stage == "rc":
            return ReleaseSupport("candidate", 0, ("rc",))
        is_lts = f"{parsed.major}.{parsed.minor}" in self.lts_lines
        return ReleaseSupport(
            "lts" if is_lts else "standard",
            self.lts_support_months if is_lts else self.stable_support_months,
            ("rc", "stable"),
        )


def normalized_content_digest(files: dict[str, bytes]) -> str:
    entries = [
        {"path": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        for name, content in sorted(files.items())
    ]
    canonical = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def manifest_policy_fields(
    policy: SupportPolicy,
    *,
    version: str,
    git_sha: str,
    migration_head: str,
    files: dict[str, bytes],
) -> dict:
    support = policy.release(version)
    identity = BuildIdentity.create(version, git_sha, migration_head)
    return {
        "eligible_channels": list(support.eligible_channels),
        "support_class": support.support_class,
        "support_months": support.support_months,
        "build_id": identity.build_id,
        "content_digest": normalized_content_digest(files),
        "upgrade_policy": {"mode": "graph"},
    }


def validate_manifest_policy(manifest: dict) -> None:
    try:
        parsed = ReleaseVersion.parse(manifest["app_version"])
        channels = manifest["eligible_channels"]
        support_class = manifest["support_class"]
        support_months = manifest["support_months"]
        if (
            not isinstance(channels, list)
            or not channels
            or len(channels) != len(set(channels))
            or not set(channels) <= {"stable", "rc", "manual"}
            or (parsed.stage == "rc" and channels != ["rc"])
            or (parsed.stage == "stable" and "stable" in channels and "rc" not in channels)
            or support_class not in {"candidate", "standard", "lts"}
            or type(support_months) is not int
            or (support_class == "candidate" and support_months != 0)
            or (support_class != "candidate" and not 1 <= support_months <= 120)
            or not isinstance(manifest["build_id"], str)
            or len(manifest["build_id"]) != 20
            or not isinstance(manifest["content_digest"], str)
            or len(manifest["content_digest"]) != 64
            or manifest["upgrade_policy"] != {"mode": "graph"}
        ):
            raise ValueError
        int(manifest["build_id"], 16)
        int(manifest["content_digest"], 16)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("invalid_release_policy") from error
