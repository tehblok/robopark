"""Capacity gate: catch secret disclosure, optimistic math and unsafe workloads."""

import asyncio
import gzip
import json
import runpy
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/capacity-gate.py"
LOCAL_BENCHMARK = Path(__file__).resolve().parents[2] / "scripts/capacity_benchmark.py"


def module():
    assert SCRIPT.is_file(), "the repeatable target-host capacity harness is missing"
    return runpy.run_path(str(SCRIPT))


def local_benchmark_module():
    return runpy.run_path(str(LOCAL_BENCHMARK))


def test_local_benchmark_help_is_renderable():
    result = subprocess.run(
        [sys.executable, str(LOCAL_BENCHMARK), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--presence" in result.stdout


def test_status_history_gate_is_scoped_to_cold_setup_before_ttl_expiry():
    coalesced = local_benchmark_module()["status_history_coalesced"]

    assert (
        coalesced(
            {"upstream_calls": {"issue_history:ROBOPARK-1": 1}},
            {"upstream_calls": {}},
        )
        is True
    )
    assert (
        coalesced(
            {"upstream_calls": {"issue_history:ROBOPARK-1": 1}},
            {"upstream_calls": {"issue_history:ROBOPARK-1": 1}},
        )
        is False
    )
    assert coalesced({"upstream_calls": {"issue_history:ROBOPARK-1": 2}}) is False
    assert coalesced({"upstream_calls": {}}) is False


def config_file(tmp_path, **changes):
    value = {"base_url": "https://park.example", "session": "fixture-session-secret"}
    value.update(changes)
    path = tmp_path / "capacity.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path


def test_nearest_rank_latency_and_error_math():
    api = module()
    samples = [api["Sample"](float(n), 200 if n != 100 else 503, body_bytes=20) for n in range(1, 101)]
    result = api["summarize"](samples, elapsed=10)
    assert result["latency_ms"] == {"p50": 50, "p95": 95, "p99": 99}
    assert result["throughput_rps"] == 10
    assert result["error_rate"] == 0.01
    assert result["requests"] == 100
    assert result["response_bytes_total"] == 2000
    assert result["response_bytes_per_second"] == 200
    assert api["summarize"]([], elapsed=1)["latency_ms"]["p95"] is None


def test_config_defaults_and_safe_public_projection(tmp_path):
    api = module()
    config = api["load_config"](config_file(tmp_path))
    assert config.users == 200
    assert config.duration_seconds == 600
    assert config.warmup_seconds == 30
    assert config.writes is False
    assert "/api/changes?scope=work:mine" in config.public()["read_paths"]
    report = json.dumps(config.public())
    assert "fixture-session-secret" not in report
    assert "park.example" not in report


@pytest.mark.parametrize(
    "changes",
    [
        {"base_url": "https://user:secret@example.com"},
        {"base_url": "https://example.com/?token=secret"},
        {"base_url": "http://example.com"},
        {"base_url": "https://example.com/private"},
        {"session": "secret\r\nX-Injected: yes"},
        {"session": ""},
        {"users": True},
        {"users": 0},
        {"users": 1001},
        {"duration_seconds": -1},
        {"think_seconds": float("nan")},
        {"write_park_id": 1},
        {"writes": True},
        {"unknown": "secret"},
    ],
)
def test_invalid_configuration_fails_without_echoing_values(tmp_path, changes):
    with pytest.raises(ValueError) as caught:
        module()["load_config"](config_file(tmp_path, **changes))
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("mode", [0o644, 0o400, 0o666])
def test_config_requires_exact_private_permissions(tmp_path, mode):
    path = config_file(tmp_path)
    path.chmod(mode)
    with pytest.raises(ValueError, match="config_permissions"):
        module()["load_config"](path)


def test_config_rejects_symlink_and_duplicate_keys(tmp_path):
    api = module()
    path = config_file(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        api["load_config"](link)
    path.write_text('{"base_url":"https://a.example","session":"one","session":"two"}')
    with pytest.raises(ValueError):
        api["load_config"](path)


def test_transport_errors_and_server_bodies_are_never_published(tmp_path):
    api = module()
    config = api["load_config"](
        config_file(tmp_path, users=2, duration_seconds=0.03, warmup_seconds=0, think_seconds=0)
    )

    def responder(request):
        assert request.headers["cookie"] == "robopark_session=fixture-session-secret"
        assert request.method == "GET"
        if request.url.path.endswith("/me"):
            raise httpx.ConnectError("fixture-session-secret", request=request)
        return httpx.Response(503, text="database is locked; Authorization: fixture-session-secret")

    result = asyncio.run(api["run_load"](config, transport=httpx.MockTransport(responder)))
    report = json.dumps(result)
    assert "fixture-session-secret" not in report
    assert result["metrics"]["error_rate"] == 1
    assert result["observed_failures"]["database_lock"] > 0
    assert result["gate"] == "FAIL"


def test_transfer_metric_counts_encoded_response_body(tmp_path):
    api = module()
    config = api["load_config"](
        config_file(tmp_path, users=1, duration_seconds=0.01, warmup_seconds=0, think_seconds=0)
    )
    encoded = gzip.compress(b"plain body" * 100)

    class EncodedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield encoded

    result = asyncio.run(api["run_load"](
        config, transport=httpx.MockTransport(lambda _request: httpx.Response(
            200, stream=EncodedStream(), headers={"content-encoding": "gzip"}
        ))
    ))
    assert result["metrics"]["requests"] > 0
    assert result["metrics"]["response_bytes_total"] == result["metrics"]["requests"] * len(encoded)


def test_no_server_evidence_can_never_pass_target_gate():
    api = module()
    metrics = {
        "requests": 100000,
        "throughput_rps": 150,
        "error_rate": 0,
        "latency_ms": {"p50": 100, "p95": 500, "p99": 900},
    }
    assert (
        api["evaluate_gate"](metrics, users=200, duration=600, warmup=30, server_evidence=None)
        == "PENDING_SERVER_EVIDENCE"
    )
    evidence = {"database_lock": 0, "event_loop": 0, "oom": 0, "restarts": 0}
    assert (
        api["evaluate_gate"](metrics, users=200, duration=600, warmup=30, server_evidence=evidence)
        == "PASS"
    )
    assert (
        api["evaluate_gate"](metrics, users=2, duration=600, warmup=30, server_evidence=evidence)
        == "FAIL"
    )
    evidence["event_loop"] = 1
    assert (
        api["evaluate_gate"](metrics, users=200, duration=600, warmup=30, server_evidence=evidence)
        == "FAIL"
    )


def test_isolated_write_run_only_updates_its_new_inactive_park(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOW_ISOLATED_WRITES", "true")
    api = module()
    config = api["load_config"](
        config_file(
            tmp_path,
            users=2,
            duration_seconds=0.02,
            warmup_seconds=0,
            think_seconds=0,
            writes=True,
            isolated_test_data=True,
        )
    )
    writes = []

    def responder(request):
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["tag"].startswith("capacity-")
            writes.append(("create", body))
            return httpx.Response(201, json={"id": 731, **body})
        if request.method == "PATCH":
            assert request.url.path == "/api/parks/731"
            writes.append(("update", json.loads(request.content)))
        return httpx.Response(200, json={})

    result = asyncio.run(api["run_load"](config, transport=httpx.MockTransport(responder)))
    assert writes[0][0] == "create"
    assert writes[1] == ("update", {"is_active": False})
    assert writes[-1] == ("update", {"is_active": False})
    assert result["isolated_park_id"] == 731


def test_server_log_scan_retains_only_failure_counts(tmp_path):
    api = module()
    log = tmp_path / "api.log"
    log.write_text(
        "secret=LEAK database is locked\nTask exception was never retrieved LEAK\nnormal\n"
    )
    evidence = api["scan_server_log"](log)
    assert evidence == {"database_lock": 1, "event_loop": 1, "oom": 0, "restarts": 0}
    assert "LEAK" not in json.dumps(evidence)


def test_writes_need_explicit_environment_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("ALLOW_ISOLATED_WRITES", raising=False)
    with pytest.raises(ValueError):
        module()["load_config"](config_file(tmp_path, writes=True, isolated_test_data=True))


def test_evaluation_rejects_injected_fields_in_saved_report(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "format": 1,
                "gate": "PASS",
                "credential": "LEAK",
                "workload": {"users": 200, "duration_seconds": 600, "warmup_seconds": 30},
                "metrics": {
                    "requests": 100000,
                    "throughput_rps": 200,
                    "error_rate": 0,
                    "latency_ms": {"p50": 100, "p95": 200, "p99": 300},
                },
                "cleanup_ok": True,
                "sample_limit_reached": False,
                "observed_failures": {},
            }
        )
    )
    log = tmp_path / "server.log"
    log.write_text("normal\n")
    output = tmp_path / "output.json"
    process = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--evaluate",
            str(report),
            "--server-log",
            str(log),
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert process.returncode == 1
    assert not output.exists()
    assert "LEAK" not in process.stdout + process.stderr


def test_write_cleanup_failure_fails_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOW_ISOLATED_WRITES", "true")
    api = module()
    config = api["load_config"](
        config_file(
            tmp_path,
            users=1,
            duration_seconds=0.005,
            warmup_seconds=0,
            think_seconds=0,
            writes=True,
            isolated_test_data=True,
        )
    )
    deactivations = 0

    def respond(request):
        nonlocal deactivations
        if request.method == "POST":
            return httpx.Response(201, json={"id": 1})
        if request.method == "PATCH" and json.loads(request.content) == {"is_active": False}:
            deactivations += 1
            return httpx.Response(200 if deactivations == 1 else 500)
        return httpx.Response(200, json={})

    report = asyncio.run(api["run_load"](config, transport=httpx.MockTransport(respond)))
    assert report["cleanup_ok"] is False
    assert report["gate"] == "FAIL"


@pytest.mark.parametrize("rejected_id", ["999", True, 0, -1, None, 999.0, 2**63, {}, []])
def test_rejected_created_park_id_never_becomes_a_cleanup_target(
    tmp_path, monkeypatch, rejected_id
):
    monkeypatch.setenv("ALLOW_ISOLATED_WRITES", "true")
    api = module()
    config = api["load_config"](
        config_file(
            tmp_path,
            users=1,
            duration_seconds=0.001,
            warmup_seconds=0,
            writes=True,
            isolated_test_data=True,
        )
    )
    commands = []

    def respond(request):
        commands.append((request.method, request.url.path))
        return httpx.Response(201, json={"id": rejected_id})

    with pytest.raises(ValueError, match="isolated_setup_failed"):
        asyncio.run(api["run_load"](config, transport=httpx.MockTransport(respond)))
    assert commands == [("POST", "/api/parks")]


@pytest.mark.parametrize("body", [[], "not-an-object", None])
def test_invalid_create_response_never_schedules_cleanup(tmp_path, monkeypatch, body):
    monkeypatch.setenv("ALLOW_ISOLATED_WRITES", "true")
    api = module()
    config = api["load_config"](
        config_file(
            tmp_path,
            users=1,
            duration_seconds=0.001,
            warmup_seconds=0,
            writes=True,
            isolated_test_data=True,
        )
    )
    commands = []

    def respond(request):
        commands.append(request.method)
        return httpx.Response(
            201, content=json.dumps(body), headers={"content-type": "application/json"}
        )

    with pytest.raises(ValueError, match="isolated_setup_failed"):
        asyncio.run(api["run_load"](config, transport=httpx.MockTransport(respond)))
    assert commands == ["POST"]


def test_measured_mutations_emit_real_sql_updates(tmp_path, monkeypatch):
    # Exercise the real ORM endpoint, not just HTTP verbs. The old constant name
    # emits one UPDATE in warmup and silently turns the measured run into reads.
    import importlib

    # Host-only pytest does not load the API conftest/main bootstrap. Initialize
    # RBAC first, as the application does, before importing its dependent router.
    importlib.import_module("robopark_api.services.rbac")
    from robopark_api.models import Base, Park
    from robopark_api.routers.parks import update_park
    from robopark_api.schemas import ParkUpdate
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import Session

    monkeypatch.setenv("ALLOW_ISOLATED_WRITES", "true")
    api = module()
    config = api["load_config"](
        config_file(
            tmp_path,
            users=3,
            duration_seconds=0.15,
            warmup_seconds=0.03,
            think_seconds=0,
            writes=True,
            isolated_test_data=True,
        )
    )
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    updates = []
    names = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda c, cur, sql, p, ctx, many: (
            updates.append(sql) if sql.startswith("UPDATE parks SET name=") else None
        ),
    )
    with Session(engine) as db:
        db.add(Park(id=731, name="new", tag="fixture", is_active=False))
        db.commit()

        def respond(request):
            if request.method == "POST":
                return httpx.Response(201, json={"id": 731})
            if request.method == "PATCH":
                assert request.url.path == "/api/parks/731"
                payload = ParkUpdate(**json.loads(request.content))
                if payload.name is not None:
                    names.append(payload.name)
                update_park(731, payload, db, None)
            return httpx.Response(200, json={})

        asyncio.run(api["run_load"](config, transport=httpx.MockTransport(respond)))
    engine.dispose()
    assert len(names) >= 3
    assert len(updates) == len(names)
    assert len(set(names)) == len(names)
    assert all(len(name) <= 120 for name in names)
