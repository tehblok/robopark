import runpy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VALIDATE_PROMOTION = runpy.run_path(str(ROOT / "scripts/release_acceptance.py"))[
    "validate_promotion_evidence"
]
PROMOTION_GATES = runpy.run_path(str(ROOT / "scripts/release_acceptance.py"))[
    "REQUIRED_PROMOTION_GATES"
]


def evidence(now):
    return {
        "format": 2,
        "source_sha": "a" * 40,
        "artifact_sha256": "b" * 64,
        "manifest_digest": "c" * 64,
        "command": "./scripts/verify.sh full",
        "started_at": (now - timedelta(hours=1)).isoformat(),
        "completed_at": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (now + timedelta(days=7)).isoformat(),
        "gates": dict.fromkeys(PROMOTION_GATES, "PASS"),
    }


def test_promotion_evidence_is_bound_to_exact_artifact_and_manifest():
    now = datetime.now(UTC)
    value = evidence(now)
    assert (
        VALIDATE_PROMOTION(
            value,
            expected_source_sha="a" * 40,
            expected_artifact_sha256="b" * 64,
            expected_manifest_digest="c" * 64,
            now=now,
        )
        == value
    )
    with pytest.raises(ValueError, match="release_acceptance_failed"):
        VALIDATE_PROMOTION(
            value,
            expected_source_sha="a" * 40,
            expected_artifact_sha256="0" * 64,
            expected_manifest_digest="c" * 64,
            now=now,
        )


def test_expired_promotion_evidence_is_rejected():
    now = datetime.now(UTC)
    value = evidence(now)
    value["expires_at"] = (now - timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="release_acceptance_failed"):
        VALIDATE_PROMOTION(
            value,
            expected_source_sha="a" * 40,
            expected_artifact_sha256="b" * 64,
            expected_manifest_digest="c" * 64,
            now=now,
        )


def test_placeholder_gate_cannot_promote_to_stable():
    now = datetime.now(UTC)
    value = evidence(now)
    value["gates"] = {"placeholder": "PASS"}
    with pytest.raises(ValueError, match="release_acceptance_failed"):
        VALIDATE_PROMOTION(
            value,
            expected_source_sha="a" * 40,
            expected_artifact_sha256="b" * 64,
            expected_manifest_digest="c" * 64,
            now=now,
        )
