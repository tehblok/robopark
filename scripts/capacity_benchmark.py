"""Real HTTP capacity exercise; NEVER targets an existing API or database.

Run with apps/api/.venv/bin/python scripts/capacity_benchmark.py --output /tmp/capacity.json
Starts two uvicorn workers and a deterministic loopback upstream, seeds a new
temporary SQLite database, runs assertions, writes JSON, and removes the fixture.
200 open clients at a 3s cadence is a different phase from a 200-request burst.
No .env, shell profiles, live credentials, real integrations or Tuna are used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
VIN = "YASADR00000000447"


class StubServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 512

    def __init__(self, delay, issue_count):
        super().__init__(("127.0.0.1", 0), StubHandler)
        self.delay = delay
        self.lock = threading.Lock()
        self.calls = Counter()
        self.workers = Counter()
        self.issues = [self.issue(i) for i in range(1, issue_count + 1)]

    @staticmethod
    def issue(index, foreign=False):
        return {
            "key": "ROBOPARK-FOREIGN" if foreign else f"ROBOPARK-{index}",
            "summary": f"Synthetic task [{447 + index % 20}]",
            "status": "Open",
            "status_key": "open",
            "queue": "ROBOPARK",
            "created": "2026-09-01T00:00:00Z",
            "hours_created": "24",
            "tags": ["CapacityForeign" if foreign else "CapacityAlpha"],
            "robot": str(447 + index % 20),
            "description": "Synthetic task body " * 20,
            "resolution": "",
            "priority": "blocker",
            "type_key": "repair",
        }

    def snapshot(self):
        with self.lock:
            return dict(self.calls)


class StubHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        operation, args = request["operation"], request["args"]
        key = args.get("key") or args.get("vin") or args.get("query") or "park"
        with self.server.lock:
            self.server.calls[f"{operation}:{key}"] += 1
            self.server.workers[str(request["worker"])] += 1
        time.sleep(self.server.delay)
        if operation == "search_issues":
            result = [*self.server.issues, self.server.issue(0, True)]
        elif operation == "get_issue":
            result = (
                self.server.issue(0, True)
                if key.endswith("FOREIGN")
                else self.server.issue(int(key.rsplit("-", 1)[1]))
            )
        elif operation == "list_comments":
            result = [
                {
                    "id": "1",
                    "text": "Synthetic comment",
                    "created_at": "2026-09-01T00:00:00Z",
                    "created_by": "capacity",
                }
            ]
        elif operation == "search_robot_tickets":
            result = [
                {
                    **self.server.issue(0, args["query"] == "999"),
                    "robot": args["query"],
                    "summary": f"Synthetic task [{args['query']}]",
                }
            ]
        elif operation == "emergency":
            result = {
                "name": "Synthetic Robot",
                "vin": args["vin"],
                "isOnline": True,
                "errors": [],
            }
        elif operation == "fetch_park_blockers":
            result = []
        elif operation == "count_issues":
            result = len(self.server.issues)
        else:
            self.send_error(500, "Unexpected stub operation")
            return
        body = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def difference(after, before):
    return {
        key: value - before.get(key, 0)
        for key, value in after.items()
        if value != before.get(key, 0)
    }


def summarize(records, elapsed, upstream):
    latencies = sorted(row["ms"] for row in records)

    def percentile(p):
        return round(latencies[max(0, math.ceil(len(latencies) * p) - 1)], 2) if latencies else None

    return {
        "requests": len(records),
        "elapsed_seconds": round(elapsed, 3),
        "throughput_rps": round(len(records) / elapsed, 2),
        "latency_ms": {
            "p50": percentile(0.50),
            "p95": percentile(0.95),
            "p99": percentile(0.99),
        },
        "status_counts": dict(Counter(str(row["status"]) for row in records)),
        "unexpected_responses": sum(not row["ok"] for row in records),
        "server_errors": sum(
            isinstance(row["status"], int) and row["status"] >= 500 for row in records
        ),
        "timeouts": sum(row["status"] == "timeout" for row in records),
        "worker_pids": sorted({row["worker"] for row in records if row["worker"]}),
        "sum_worker_peak_rss_mb": round(
            sum(
                max(row["rss_bytes"] for row in records if row["worker"] == pid)
                for pid in {row["worker"] for row in records if row["worker"]}
            )
            / 1024**2,
            2,
        ),
        "upstream_calls": upstream,
        "upstream_calls_total": sum(upstream.values()),
        "failures": [row for row in records if not row["ok"]][:10],
        "by_route": {
            route: {
                "requests": sum(row["route"] == route for row in records),
                "unexpected": sum(row["route"] == route and not row["ok"] for row in records),
            }
            for route in sorted({row["route"] for row in records})
        },
    }


async def exercise(base, args, stub):
    timeout = httpx.Timeout(args.timeout)
    clients = [
        httpx.AsyncClient(
            base_url=base,
            timeout=timeout,
            trust_env=False,
            headers={"Cookie": f"robopark_session=capacity-session-{i}"},
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2, keepalive_expiry=4),
        )
        for i in range(args.users)
    ]
    phases = {}

    async def request(index, route, method="GET", payload=None, expected=200):
        start = time.perf_counter()
        status, worker, error, semantic = "transport_error", None, None, True
        rss_bytes = 0
        try:
            response = await clients[index].request(method, route, json=payload)
            status = response.status_code
            worker = response.headers.get("X-Capacity-Worker")
            rss_bytes = int(response.headers.get("X-Capacity-Peak-Rss-Bytes", 0))
            if status == 200 and route.startswith("/tracker/issues?"):
                body = response.json()
                semantic = body["total"] == args.issues and all(
                    item["key"] != "ROBOPARK-FOREIGN" for item in body["items"]
                )
            if status != expected or not semantic:
                error = response.text[:200]
        except httpx.TimeoutException:
            status = "timeout"
        except httpx.HTTPError as exc:
            error = type(exc).__name__
        return {
            "route": route,
            "status": status,
            "worker": worker,
            "ms": (time.perf_counter() - start) * 1000,
            "rss_bytes": rss_bytes,
            "ok": status == expected and semantic,
            "error": error,
        }

    async def phase(name, operation):
        before = stub.snapshot()
        start = time.perf_counter()
        records = await operation()
        phases[name] = summarize(
            records, time.perf_counter() - start, difference(stub.snapshot(), before)
        )
        print(
            json.dumps(
                {
                    "phase": name,
                    **{
                        key: value
                        for key, value in phases[name].items()
                        if key not in {"by_route", "upstream_calls"}
                    },
                }
            ),
            flush=True,
        )

    async def burst(route):
        return await asyncio.gather(*(request(i, route) for i in range(args.users)))

    try:
        await phase("cold_reconnect_200_requests", lambda: burst("/tracker/issues?limit=50"))
        await phase("cold_details_200_requests", lambda: burst("/tracker/issues/ROBOPARK-1"))
        await phase("cold_robot_check_200_requests", lambda: burst(f"/emergency/{VIN}/snapshot"))

        async def scoped_denials():
            routes = [
                "/tracker/issues/ROBOPARK-FOREIGN",
                "/tracker/issues/ROBOPARK-FOREIGN/comments",
                "/emergency/YASADR00000000999/snapshot",
            ]
            return await asyncio.gather(
                *(request(i, routes[i % len(routes)], expected=403) for i in range(args.users))
            )

        await phase("scoped_denial_200_requests", scoped_denials)

        async def cadence():
            started = time.perf_counter()
            routes = [
                "/tracker/issues?limit=50",
                "/tracker/issues/ROBOPARK-1",
                "/tracker/issues/ROBOPARK-1/comments",
                "/reports/inbox",
                f"/emergency/{VIN}/snapshot",
            ]

            async def user_loop(index):
                records = []
                issue_key = f"ROBOPARK-{index % args.active_records + 1}"
                user_vin = f"YASADR{447 + index % args.active_records:011d}"
                user_routes = [
                    routes[0],
                    f"/tracker/issues/{issue_key}",
                    f"/tracker/issues/{issue_key}/comments",
                    routes[3],
                    f"/emergency/{user_vin}/snapshot",
                ]
                due = started + args.cadence * index / args.users
                turn = 0
                while due < started + args.duration:
                    await asyncio.sleep(max(0, due - time.perf_counter()))
                    if turn == 1 and index % 10 == 0:
                        records.append(
                            await request(
                                index,
                                "/reports",
                                "POST",
                                {
                                    "park_id": 1,
                                    "kind": "mechanic_problem",
                                    "title": f"Capacity local write {index}",
                                    "body": "Synthetic local write",
                                },
                                expected=201,
                            )
                        )
                    elif args.presence and (index + turn) % 10 == 0:
                        records.append(
                            await request(index, f"/tracker/issues/{issue_key}/presence", "POST")
                        )
                    else:
                        records.append(
                            await request(index, user_routes[(index + turn) % len(user_routes)])
                        )
                    turn += 1
                    # No catch-up avalanche if overloaded; report achieved RPS.
                    due = max(due + args.cadence, time.perf_counter())
                return records

            batches = await asyncio.gather(*(user_loop(i) for i in range(args.users)))
            return [row for batch in batches for row in batch]

        await phase("200_open_clients_realistic_cadence", cadence)

        async def stress():
            routes = [
                "/tracker/issues?limit=50",
                "/tracker/issues/ROBOPARK-1",
                "/tracker/issues/ROBOPARK-1/comments",
                "/reports/inbox",
                f"/emergency/{VIN}/snapshot",
            ]
            records = []
            for _ in range(args.stress_rounds):
                records.extend(
                    await asyncio.gather(*(request(i, routes[i % 5]) for i in range(args.users)))
                )
            return records

        await phase("200_concurrent_requests_stress", stress)
    finally:
        await asyncio.gather(*(client.aclose() for client in clients))
    return phases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/tmp/robopark-capacity.json"))
    parser.add_argument("--users", type=int, default=200)
    parser.add_argument("--issues", type=int, default=200)
    parser.add_argument(
        "--active-records",
        type=int,
        default=20,
        help="Distinct task and robot keys in the sustained mix (at most --issues)",
    )
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--cadence", type=float, default=3)
    parser.add_argument("--stub-delay-ms", type=float, default=100)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--stress-rounds", type=int, default=3)
    parser.add_argument(
        "--presence",
        action="store_true",
        help="Replace about 10% of sustained reads with task-presence writes",
    )
    args = parser.parse_args()
    if (
        min(
            args.users,
            args.issues,
            args.duration,
            args.cadence,
            args.timeout,
            args.stress_rounds,
        )
        <= 0
        or args.stub_delay_ms < 0
    ):
        parser.error("Counts and durations must be positive")
    if not 1 <= args.active_records <= args.issues:
        parser.error("--active-records must be between 1 and --issues")
    stub = StubServer(args.stub_delay_ms / 1000, args.issues)
    thread = threading.Thread(target=stub.serve_forever, daemon=True)
    thread.start()
    result = {
        "started_at": datetime.now(UTC).isoformat(),
        "configuration": {
            "users": args.users,
            "distinct_sessions": args.users,
            "uvicorn_workers": 2,
            "issues": args.issues,
            "initial_reports": args.users,
            "cadence_distinct_issue_keys": min(args.active_records, args.users),
            "cadence_distinct_robot_vins": min(args.active_records, args.users),
            "duration_seconds": args.duration,
            "cadence_seconds": args.cadence,
            "target_cadence_rps": args.users / args.cadence,
            "presence_writes": args.presence,
            "upstream_stub_delay_ms": args.stub_delay_ms,
            "timeout_seconds": args.timeout,
            "stress_rounds": args.stress_rounds,
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "network": "loopback only; excludes Tuna, browser rendering and real upstream limits",
        },
    }
    try:
        with tempfile.TemporaryDirectory(prefix="robopark-capacity-") as temp:
            root = Path(temp)
            (root / "capacity-isolated").touch()
            (root / "capacity-config.json").write_text(
                json.dumps(
                    {
                        "root": str(root),
                        "users": args.users,
                        "stub_url": f"http://127.0.0.1:{stub.server_port}/",
                    }
                )
            )
            env = {
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "PYTHONPATH": f"{REPO / 'apps/api/src'}{os.pathsep}{REPO / 'scripts'}",
                "PYTHONUNBUFFERED": "1",
                "ROBOPARK_LIVE_MERGE": "1",
            }
            subprocess.run(
                [sys.executable, str(REPO / "scripts/capacity_app.py"), "--seed"],
                cwd=root,
                env=env,
                check=True,
            )
            with socket.socket() as reserved:
                reserved.bind(("127.0.0.1", 0))
                port = reserved.getsockname()[1]
            base = f"http://127.0.0.1:{port}"
            with (root / "uvicorn.log").open("w+") as log:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "capacity_app:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--workers",
                        "2",
                        "--no-access-log",
                        "--log-level",
                        "warning",
                    ],
                    cwd=root,
                    env=env,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
                try:
                    deadline = time.monotonic() + 30
                    ready_workers = set()
                    with httpx.Client(trust_env=False, timeout=1) as client:
                        while time.monotonic() < deadline:
                            try:
                                response = client.get(
                                    base + "/health", headers={"Connection": "close"}
                                )
                                if response.status_code == 200:
                                    ready_workers.add(response.headers.get("X-Capacity-Worker"))
                                    if len(ready_workers) == 2:
                                        break
                            except httpx.HTTPError:
                                pass
                            if process.poll() is not None:
                                raise RuntimeError("uvicorn exited during startup")
                            time.sleep(0.05)
                        else:
                            raise RuntimeError("Both uvicorn workers did not become ready")
                    result["phases"] = asyncio.run(exercise(base, args, stub))
                    with sqlite3.connect(root / "capacity.db") as db:
                        result["database"] = {
                            "journal_mode": db.execute("PRAGMA journal_mode").fetchone()[0],
                            "users": db.execute("SELECT count(*) FROM users").fetchone()[0],
                            "sessions": db.execute("SELECT count(*) FROM sessions").fetchone()[0],
                            "reports_after": db.execute("SELECT count(*) FROM reports").fetchone()[
                                0
                            ],
                            "presence_rows": db.execute(
                                "SELECT count(*) FROM tracker_presence"
                            ).fetchone()[0],
                            "integrity_check": db.execute("PRAGMA integrity_check").fetchone()[0],
                        }
                finally:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
                    log.seek(0)
                    result["server_log_tail"] = log.read()[-12000:]
    except Exception as exc:  # noqa: BLE001 -- preserve diagnostic output and tear down the fixture
        result["harness_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        stub.shutdown()
        stub.server_close()
    phases = result.get("phases", {})
    cadence = phases.get("200_open_clients_realistic_cadence", {})
    result["acceptance"] = {
        "all_requests_match_contract": bool(phases)
        and all(p["unexpected_responses"] == 0 for p in phases.values()),
        "both_workers_served_load": len(
            {pid for phase in phases.values() for pid in phase["worker_pids"]}
        )
        == 2,
        "bursts_p95_under_5000ms": bool(phases)
        and all(
            (phase["latency_ms"]["p95"] or math.inf) < 5000
            for name, phase in phases.items()
            if name != "200_open_clients_realistic_cadence"
        ),
        "cadence_p95_under_1000ms": (cadence.get("latency_ms", {}).get("p95") or math.inf) < 1000,
        "cadence_achieves_90_percent_target": cadence.get("throughput_rps", 0)
        >= 0.9 * args.users / args.cadence,
        "cold_search_coalesced_to_one": sum(
            v
            for k, v in phases.get("cold_reconnect_200_requests", {})
            .get("upstream_calls", {})
            .items()
            if k.startswith("search_issues:")
        )
        == 1,
        "cold_detail_coalesced_to_one": sum(
            v
            for k, v in phases.get("cold_details_200_requests", {})
            .get("upstream_calls", {})
            .items()
            if k.startswith("get_issue:ROBOPARK-1")
        )
        == 1,
        "cold_robot_coalesced_within_ttl": 1
        <= phases.get("cold_robot_check_200_requests", {})
        .get("upstream_calls", {})
        .get(f"emergency:{VIN}", 0)
        <= math.ceil(
            phases.get("cold_robot_check_200_requests", {}).get("elapsed_seconds", 0) / 2.5
        ),
        "denied_comments_and_emergency_never_fetched": not any(
            k.startswith(("list_comments:ROBOPARK-FOREIGN", "emergency:YASADR00000000999"))
            for k in stub.snapshot()
        ),
        "sqlite_integrity_ok": result.get("database", {}).get("integrity_check") == "ok",
        "local_writes_persisted": result.get("database", {}).get("reports_after", 0) > args.users,
        "presence_writes_persisted": not args.presence
        or result.get("database", {}).get("presence_rows", 0) > 0,
        "no_harness_error": "harness_error" not in result,
    }
    result["passed"] = all(result["acceptance"].values())
    result["upstream_total"] = stub.snapshot()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "passed": result["passed"],
                "acceptance": result["acceptance"],
            },
            indent=2,
        )
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
