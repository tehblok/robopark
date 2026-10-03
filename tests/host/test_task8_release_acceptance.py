"""Task 8 release evidence and resumable soak contracts."""

from __future__ import annotations

import hashlib
import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE = ROOT / "scripts/release_acceptance.py"
SOAK = ROOT / "scripts/cache_soak.py"


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
        report.write_text(json.dumps({"passed": True, "source_tree_sha256": tree}))

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


def test_acceptance_cannot_rebind_old_gate_reports_to_changed_sources(tmp_path):
    api = acceptance_module()
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("reviewed source\n")
    old_tree = api["source_tree_digest"](tmp_path, ["tracked.txt"])
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"passed": True, "source_tree_sha256": old_tree}))

    tracked.write_text("changed after gates ran\n")
    new_tree = api["source_tree_digest"](tmp_path, ["tracked.txt"])
    evidence = {
        "format": 1,
        "source_tree_sha256": new_tree,
        "source_paths": ["tracked.txt"],
        "gates": {
            name: {"status": "PASS", "report": "report.json"}
            for name in api["REQUIRED_GATES"]
        },
    }

    with pytest.raises(ValueError, match="acceptance_report_source_tree"):
        api["validate_acceptance"](tmp_path, evidence)


def test_acceptance_rejects_symlink_or_report_without_pass(tmp_path):
    api = acceptance_module()
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("source\n")
    report = tmp_path / "report.json"
    tree = api["source_tree_digest"](tmp_path, ["tracked.txt"])
    report.write_text(json.dumps({"passed": False, "source_tree_sha256": tree}))
    evidence = {
        "format": 1,
        "source_tree_sha256": tree,
        "source_paths": ["tracked.txt"],
        "gates": {
            name: {"status": "PASS", "report": "report.json"}
            for name in api["REQUIRED_GATES"]
        },
    }
    with pytest.raises(ValueError, match="acceptance_report"):
        api["validate_acceptance"](tmp_path, evidence)

    report.write_text(json.dumps({"passed": True, "source_tree_sha256": tree}))
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
    tree = api["source_tree_digest"](tmp_path, ["tracked.txt"])
    passed.write_text(json.dumps({"passed": True, "source_tree_sha256": tree}))
    cancelled = tmp_path / "cancelled.json"
    cancelled.write_text(
        json.dumps(
            {
                "passed": False,
                "ruling": "USER_CANCELLED",
                "elapsed_seconds": 4800,
                "source_tree_sha256": tree,
            }
        )
    )
    evidence = {
        "format": 1,
        "source_tree_sha256": tree,
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
    assert api["is_release_source"]("apps/bot/entrypoint.py") is True
    assert api["is_release_source"]("deploy/docker-compose.prod.yml") is True
    assert api["is_release_source"]("scripts/release_pack.py") is True
    assert api["is_release_source"]("VERSION") is True
    assert api["is_release_source"]("apps/web/tmp/soak-smoke.json") is False
    assert api["is_release_source"]("apps/api/.env") is False
    assert api["is_release_source"]("apps/api/.env.example") is True
    assert api["is_release_source"]("deploy/private.pem") is False
    assert api["is_release_source"]("../scripts/release_pack.py") is False
    assert api["is_release_source"]("/scripts/release_pack.py") is False
    assert (
        api["is_release_source"]("docs/product-completion/evidence/gate.json") is False
    )
    assert api["is_release_source"](".superpowers/sdd/progress.md") is False


def test_source_enumeration_includes_bot_and_untracked_source_but_skips_deleted_and_artifacts(
    tmp_path,
):
    api = acceptance_module()
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    files = {
        "apps/api/main.py": "api source\n",
        "apps/bot/entrypoint.py": "bot source\n",
        "apps/web/deleted.ts": "deleted source\n",
    }
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    (tmp_path / "apps/web/deleted.ts").unlink()
    untracked = {
        "scripts/new_release_tool.py": "source\n",
        "apps/web/tmp/soak-smoke.json": "{}\n",
        "apps/api/.env": "SECRET=never-read\n",
        "deploy/private.pem": "never-read\n",
    }
    for relative, content in untracked.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    assert api["tracked_source_paths"](tmp_path, set()) == [
        "apps/api/main.py",
        "apps/bot/entrypoint.py",
        "scripts/new_release_tool.py",
    ]
    alias = tmp_path / "apps/api/alias.py"
    alias.symlink_to("main.py")
    with pytest.raises(ValueError, match="acceptance_report"):
        api["source_tree_digest"](tmp_path, ["apps/api/alias.py"])


def test_current_ota_source_filter_excludes_local_artifacts_and_secrets():
    from scripts.build_ota import include_source_path

    assert include_source_path(Path("apps/api/src/robopark_api/main.py")) is True
    assert include_source_path(Path("deploy/systemd/robopark.service")) is True
    assert include_source_path(Path("apps/web/node_modules/pkg/index.js")) is False
    assert include_source_path(Path("output/update/candidate.ota")) is False
    assert include_source_path(Path("deploy/private.pem")) is False
    assert include_source_path(Path("deploy/.env")) is False


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
            "browser_rss_bytes": 100_000_000 + index * 10_000,
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
    assert result["growth"]["browser_rss_bytes"] == 50_000

    leaking = [
        dict(sample, object_url_count=index) for index, sample in enumerate(samples)
    ]
    result = api["evaluate_samples"](leaking, warmup_samples=2)
    assert result["passed"] is False
    assert "object_url_count" in result["monotonic_growth"]

    # A small GC dip must not hide a large sustained leak.
    sawtooth = [dict(samples[0], browser_rss_bytes=value * 1024**2)
                for value in [100, 250, 249, 500]]
    result = api["evaluate_samples"](sawtooth, warmup_samples=0)
    assert result["passed"] is False
    assert "browser_rss_bytes" in result["excessive_growth"]


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


def test_continuous_browser_report_requires_one_context_complete_operations_and_samples():
    api = soak_module()
    sample = {
        "browser_rss_bytes": 100_000_000,
        "fd_count": 30,
        "timer_count": 4,
        "subscription_count": 8,
        "object_url_count": 0,
        "media_track_count": 0,
        "cache_bytes": 4096,
        "storage_bytes": 8192,
        "db_pool_checked_out": 0,
        "errors": 0,
        "operations": sorted(api["REQUIRED_OPERATIONS"]),
    }
    report = {
        "format": 2,
        "run_token": "run-token",
        "target_seconds": 600,
        "elapsed_seconds": 600,
        "continuous_contexts": 1,
        "samples": [
            {**sample, "elapsed_seconds": 300},
            {**sample, "elapsed_seconds": 600},
        ],
    }
    assert api["validate_continuous_report"](report, duration_seconds=600, run_token="run-token") == report

    report["continuous_contexts"] = 2
    with pytest.raises(ValueError, match="soak_continuous"):
        api["validate_continuous_report"](report, duration_seconds=600, run_token="run-token")

    report["continuous_contexts"] = 1
    report["samples"][-1]["operations"] = ["navigation"]
    with pytest.raises(ValueError, match="soak_sample"):
        api["validate_continuous_report"](report, duration_seconds=600, run_token="run-token")


def test_continuous_producer_cannot_claim_time_that_did_not_elapse(tmp_path):
    api = soak_module()
    producer = tmp_path / "producer.py"
    producer.write_text('''import json, os
from pathlib import Path
sample = dict(browser_rss_bytes=1000000, fd_count=4, timer_count=1,
subscription_count=1, object_url_count=0, media_track_count=0, cache_bytes=0,
storage_bytes=0, db_pool_checked_out=0, errors=0,
operations=["navigation", "mode_switch", "task", "robot", "cache", "background", "camera", "photo"])
Path(os.environ["ROBOPARK_SOAK_OUTPUT"]).write_text(json.dumps(dict(format=2,
run_token=os.environ["ROBOPARK_SOAK_RUN_TOKEN"], target_seconds=28800,
elapsed_seconds=28800, continuous_contexts=1,
samples=[dict(sample, elapsed_seconds=t) for t in [9600, 19200, 28800]])))
''')
    with pytest.raises(ValueError, match="soak_duration_not_real"):
        api["run_continuous"](
            [sys.executable, str(producer)], duration_seconds=28_800,
            output=tmp_path / "report.json", run_token="real-run",
        )
