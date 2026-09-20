"""Task 8 release evidence and resumable soak contracts."""

from __future__ import annotations

import hashlib
import json
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE = ROOT / "scripts/release_acceptance.py"
SOAK = ROOT / "scripts/cache_soak.py"
PACKER = ROOT / "scripts/release_pack.py"


def acceptance_module():
    return runpy.run_path(str(ACCEPTANCE))


def soak_module():
    return runpy.run_path(str(SOAK))


def test_acceptance_manifest_requires_every_release_gate_and_current_source_tree(
    tmp_path,
):
    api = acceptance_module()
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("reviewed source\n")
    tree = api["source_tree_digest"](tmp_path, ["tracked.txt"])
    evidence = {
        "format": 1,
        "source_tree_sha256": tree,
        "source_paths": ["tracked.txt"],
        "gates": {
            name: {"status": "PASS", "report": f"evidence/{name}.json"}
            for name in api["REQUIRED_GATES"]
        },
    }
    for gate in evidence["gates"].values():
        report = tmp_path / gate["report"]
        report.parent.mkdir(exist_ok=True)
        report.write_text('{"passed":true}\n')

    api["validate_acceptance"](tmp_path, evidence)

    evidence["gates"].pop("soak_8h")
    with pytest.raises(ValueError, match="acceptance_gates"):
        api["validate_acceptance"](tmp_path, evidence)

    evidence["gates"]["soak_8h"] = {
        "status": "PASS",
        "report": "evidence/soak_8h.json",
    }
    tracked.write_text("changed after evidence\n")
    with pytest.raises(ValueError, match="acceptance_source_tree"):
        api["validate_acceptance"](tmp_path, evidence)


def test_acceptance_rejects_symlink_or_report_without_pass(tmp_path):
    api = acceptance_module()
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("source\n")
    report = tmp_path / "report.json"
    report.write_text('{"passed":false}\n')
    evidence = {
        "format": 1,
        "source_tree_sha256": api["source_tree_digest"](tmp_path, ["tracked.txt"]),
        "source_paths": ["tracked.txt"],
        "gates": {
            name: {"status": "PASS", "report": "report.json"}
            for name in api["REQUIRED_GATES"]
        },
    }
    with pytest.raises(ValueError, match="acceptance_report"):
        api["validate_acceptance"](tmp_path, evidence)

    report.write_text('{"passed":true}\n')
    alias = tmp_path / "alias.json"
    alias.symlink_to(report)
    evidence["gates"]["full_api"] = {"status": "PASS", "report": "alias.json"}
    with pytest.raises(ValueError, match="acceptance_report"):
        api["validate_acceptance"](tmp_path, evidence)


def test_acceptance_records_explicit_user_cancelled_soak_without_calling_it_pass(
    tmp_path,
):
    api = acceptance_module()
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("source\n")
    passed = tmp_path / "passed.json"
    passed.write_text('{"passed":true}\n')
    cancelled = tmp_path / "cancelled.json"
    cancelled.write_text(
        '{"passed":false,"ruling":"USER_CANCELLED","elapsed_seconds":4800}\n'
    )
    evidence = {
        "format": 1,
        "source_tree_sha256": api["source_tree_digest"](tmp_path, ["tracked.txt"]),
        "source_paths": ["tracked.txt"],
        "gates": {
            name: {"status": "PASS", "report": "passed.json"}
            for name in api["REQUIRED_GATES"]
        },
    }
    evidence["gates"]["soak_8h"] = {
        "status": "USER_CANCELLED",
        "report": "cancelled.json",
    }
    api["validate_acceptance"](tmp_path, evidence)

    evidence["gates"]["full_api"]["status"] = "USER_CANCELLED"
    with pytest.raises(ValueError, match="acceptance_gates"):
        api["validate_acceptance"](tmp_path, evidence)

    assert api["gate_status"]("soak_8h", json.loads(cancelled.read_text())) == (
        "USER_CANCELLED"
    )
    assert api["gate_status"]("full_api", {"passed": False}) == "PASS"


def test_release_source_scope_excludes_evidence_and_unrelated_worktree_notes():
    api = acceptance_module()
    assert api["is_release_source"]("apps/api/pyproject.toml") is True
    assert api["is_release_source"]("deploy/docker-compose.prod.yml") is True
    assert api["is_release_source"]("scripts/release_pack.py") is True
    assert api["is_release_source"]("VERSION") is True
    assert (
        api["is_release_source"]("docs/product-completion/evidence/gate.json") is False
    )
    assert api["is_release_source"](".superpowers/sdd/progress.md") is False


def test_repository_release_pack_requires_valid_acceptance_evidence(tmp_path):
    acceptance = acceptance_module()
    packer = runpy.run_path(str(PACKER))
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("source\n")
    report = tmp_path / "report.json"
    report.write_text('{"passed":true}\n')
    evidence = {
        "format": 1,
        "source_tree_sha256": acceptance["source_tree_digest"](
            tmp_path, ["tracked.txt"]
        ),
        "source_paths": ["tracked.txt"],
        "gates": {
            name: {"status": "PASS", "report": "report.json"}
            for name in acceptance["REQUIRED_GATES"]
        },
    }
    evidence_path = tmp_path / "acceptance.json"
    evidence_path.write_text(json.dumps(evidence))

    assert (
        packer["require_acceptance"](
            SimpleNamespace(repository=True, root=tmp_path, acceptance=evidence_path)
        )["source_tree_sha256"]
        == evidence["source_tree_sha256"]
    )
    with pytest.raises(ValueError, match="acceptance_required"):
        packer["require_acceptance"](
            SimpleNamespace(repository=True, root=tmp_path, acceptance=None)
        )
    assert (
        packer["require_acceptance"](
            SimpleNamespace(repository=False, root=tmp_path, acceptance=None)
        )
        is None
    )


def test_clean_release_excludes_acceptance_and_load_tooling():
    packer = runpy.run_path(str(PACKER))
    excluded = packer["excluded"]
    assert excluded(Path("scripts/capacity_benchmark.py")) is True
    assert excluded(Path("scripts/capacity_app.py")) is True
    assert excluded(Path("scripts/cache_soak.py")) is True
    # The clean release remains independently repackable, so its acceptance
    # validator is runtime release tooling rather than a dev/load helper.
    assert excluded(Path("scripts/release_acceptance.py")) is False
    assert excluded(Path("scripts/verify-artifact.py")) is False


def test_soak_checkpoint_resumes_and_rejects_unbounded_growth(tmp_path):
    api = soak_module()
    checkpoint = tmp_path / "checkpoint.json"
    initial = api["new_checkpoint"](
        source_tree_sha256="a" * 64,
        duration_seconds=28_800,
        chunk_seconds=300,
    )
    api["write_checkpoint"](checkpoint, initial)
    resumed = api["read_checkpoint"](checkpoint)
    assert resumed["elapsed_seconds"] == 0
    assert resumed["target_seconds"] == 28_800

    samples = [
        {
            "elapsed_seconds": index * 300,
            "rss_bytes": 100_000_000 + index * 10_000,
            "fd_count": 30,
            "timer_count": 4,
            "subscription_count": 8,
            "object_url_count": 0,
            "media_track_count": 0,
            "cache_bytes": 4096,
            "storage_bytes": 8192,
            "db_pool_checked_out": 0,
            "errors": 0,
        }
        for index in range(8)
    ]
    result = api["evaluate_samples"](samples, warmup_samples=2)
    assert result["passed"] is True
    assert result["growth"]["rss_bytes"] == 50_000

    leaking = [
        dict(sample, object_url_count=index) for index, sample in enumerate(samples)
    ]
    result = api["evaluate_samples"](leaking, warmup_samples=2)
    assert result["passed"] is False
    assert "object_url_count" in result["monotonic_growth"]


def test_soak_checkpoint_checksum_detects_partial_or_tampered_write(tmp_path):
    api = soak_module()
    checkpoint = tmp_path / "checkpoint.json"
    value = api["new_checkpoint"](
        source_tree_sha256="b" * 64,
        duration_seconds=28_800,
        chunk_seconds=300,
    )
    api["write_checkpoint"](checkpoint, value)
    raw = json.loads(checkpoint.read_text())
    raw["elapsed_seconds"] = 28_800
    checkpoint.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="checkpoint_checksum"):
        api["read_checkpoint"](checkpoint)


def test_soak_report_hash_is_stable_for_machine_readable_handoff():
    api = soak_module()
    value = api["new_checkpoint"](
        source_tree_sha256="c" * 64,
        duration_seconds=28_800,
        chunk_seconds=300,
    )
    digest = api["checkpoint_digest"](value)
    assert digest == hashlib.sha256(api["canonical_json"](value)).hexdigest()
