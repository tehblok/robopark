# Release 0.1.45 acceptance

Status: **ACCEPTED WITH USER RULING**. The 8-hour soak started at `2026-09-20T09:38:15Z` and was explicitly stopped by the user after 4,800 seconds. It is recorded as `USER_CANCELLED`, not PASS; the user directed release completion without spending the remaining wall time.

## Gates completed

| Gate | Evidence | Result |
| --- | --- | --- |
| PostgreSQL API | `evidence/api-postgres.json` | 7 passed |
| Full host | `evidence/host.json` | 763 passed |
| Full API | `evidence/full-api.json` | 1968 passed / 8 skipped |
| Full web | `evidence/full-web.json` | 153 files / 2131 tests passed |
| Production build | `evidence/production-build.json` | TypeScript, Vite and service worker passed |
| Route/role/state | `evidence/route-role-evidence.json` | 65 passed, both interface modes |
| Visual A | `RELEASE_0.1.45_VISUAL_LEDGER.md`, `evidence/visual-ledger.json` | 71 passed; no unresolved structural deviation |
| 200-session load | `evidence/load-200.json` | PASS |
| 8-hour soak | `evidence/soak-8h.json`, `evidence/soak-checkpoint.json` | **USER_CANCELLED**, 4,800 / 28,800 s; not PASS |

All non-soak test gates are complete.

## Isolated PostgreSQL 17 load result

The final run used 200 independent users/cookie sessions, two Uvicorn workers, 200 tasks/reports, 20 active task and VIN keys, 60 seconds of cadence at one request per user every three seconds, 35 ms delay per network attempt and deterministic 1% Wi‑Fi loss with one retry. Stress retained all 200 sessions with a maximum of 20 requests in flight, matching the bounded DB/thread pools.

| Phase | Requests | RPS | p50 / p95 / p99, ms | Errors |
| --- | ---: | ---: | --- | ---: |
| Cold list | 200 | 83.70 | 187.95 / 475.45 / 627.21 | 0 |
| Cold task | 200 | 173.40 | 93.23 / 247.07 / 278.36 | 0 |
| Cold robot | 200 | 170.17 | 83.11 / 383.31 / 389.93 | 0 |
| Warm list | 200 | 91.46 | 206.41 / 343.77 / 435.67 | 0 |
| Warm task | 200 | 196.05 | 93.18 / 134.83 / 180.73 | 0 |
| Warm robot | 200 | 193.79 | 79.02 / 250.44 / 264.22 | 0 |
| Realistic cadence | 4000 | 66.58 | 57.60 / 166.19 / 205.17 | 0 |
| Bounded stress | 600 | 197.14 | 90.19 / 156.07 / 254.70 | 0 |

There were zero unexpected responses, 5xx, timeouts, cross-park leaks and duplicate mutations. Both workers served traffic; 20 report writes and 200 presence writes persisted exactly once. PostgreSQL reported 200 users, 200 sessions, 220 reports, 200 presence rows and an integrity result of `ok`.

Measured maxima: DB pool checkout 10 per worker, combined worker peak RSS 296.73 MiB, CPU 61.688%, final cache hit ratio 0.7903, PostgreSQL storage 11,171,507 bytes. The cadence transferred 77,713,564 bytes and made 693 upstream stub calls; the bounded stress transferred 12,819,720 bytes and made 5 upstream calls. These are measured limits on this Mac loopback fixture, not invented ARM64 latency targets and not a Tuna/real-upstream claim.

## Soak handoff

The checkpoint is atomic and checksummed and was bound to source digest `265aadf101b59a34c8d6c6164dc3405e4fe6f8c9a99c062dd850c8fa26e64939`, target 28,800 seconds, chunk 300 seconds. Sixteen real samples covered navigation, interface mode, task, robot, photo, camera, cache and background polling and recorded RSS, file descriptors, timers, subscriptions, object URLs, media tracks, storage/cache, DB pool and errors. Across the partial observation there were zero errors and no monotonic resource growth; these facts are retained only as partial evidence and are not relabelled as an 8-hour result.

## Release blocker

`release_pack.py --repository` requires current-source evidence for all nine gates. Eight gates require PASS. Only the soak gate accepts the exact fail-closed `USER_CANCELLED` ruling with `passed=false`, because the user explicitly stopped it; no other gate can use that status. The release and installer remain signed and content-verified, but the deviation must stay visible in the final handoff.
