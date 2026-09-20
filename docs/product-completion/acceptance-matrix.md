# Offline-first PWA acceptance matrix

Evidence is recorded only after the named command exits successfully. The release does not require an unattended soak.

## Current-source status

The current audit work is based on Git HEAD `d00cd1dda1d5b1e4d65075566406743a8bdc3298`
plus the Task 9 review fixes. No broad gate below is a current-source PASS. The
stored full-suite, PostgreSQL, host, visual and 200-user records are historical:
they belong to source-tree SHA-256
`c4591526ecdc5bcc56ba7608d8b7336a3726e6f9f35599a8546961b4457d74e0`.
The partial soak belongs to a different source hash and ended `USER_CANCELLED`.

| Current gate | Status |
|---|---|
| Full API / full web / production build | **UNVERIFIED for current source** |
| PostgreSQL / host / route-role / visual | **UNVERIFIED for current source** |
| 200-user load | **UNVERIFIED for current source** |
| 8-hour soak | **NOT RUN for current source**; historical attempt was cancelled |
| Targeted Task 9 checks | Recorded in the Task 9 report; scoped evidence only, never a release PASS |

## Verification targets

| Target | Purpose | Expected duration | What it deliberately excludes |
|---|---|---:|---|
| `./scripts/verify.sh fast` | Focused regression and static checks for a local change or PR | under 2 minutes on a warm developer machine | Docker, PostgreSQL container, build images, browser install, capacity and soak |
| `./scripts/verify.sh full` | Full application, PostgreSQL, web, Docker and host gate | many minutes | Capacity and soak remain separate |
| `./scripts/verify.sh load` | Disposable 200-user capacity benchmark | several minutes | Existing host, Tracker and production data |
| `./scripts/verify.sh soak` | Explicit browser endurance run | caller-selected | It refuses to start without duration and output path |

`all` remains an alias for `full` for compatibility. Neither `fast` nor the default contributor workflow may start a load or soak run implicitly.

| Area | Acceptance | Evidence |
|---|---|---|
| Scoped local data | Account/role/permission/park isolation; pending actions survive restart; revoked scope is purged | `apps/web/src/pwa/offlineDb.test.ts`, `deviceResourceCache.test.ts` |
| Idempotent sync | Duplicate delivery cannot repeat a transition or stock write-off; causal dependencies are stable | `apps/api/tests/test_offline_sync.py`, `test_sync_load.py` |
| Single client engine | One leader across tabs; bounded weak-link batches; listeners and requests are released | `apps/web/src/pwa/syncCoordinator.test.ts`, `syncEngine.test.ts`, `performanceBudget.test.ts` |
| Offline work cycle | Comment, handoff, stock write-off, defect code, one photo and review queue without page reload | work/inventory Vitest suites; reconnect path in `pwa-offline.spec.ts` |
| Camera and media | Camera/file fallback, local decoder, worker image preparation, resumable checksummed upload, bounded cleanup | media/scanner tests; API media upload tests |
| Installable shell | Warm navigation works offline; no authenticated API response enters CacheStorage; safe worker activation | service worker build tests, registration tests, `pwa-offline.spec.ts` |
| Schedules | Self-service shifts/leave/sick; admin scoped read; royal edit, copy and bulk assignment | API and web schedule tests |
| Notifications | New tasks, report/review actions and host-health transitions; role/on-shift targeting, internal fallback and explicit permission request | API push, tracker-read, system-notification and web notification tests |
| Host resources | Expired receipts/uploads and confirmed temporary files are deleted in bounded batches; pending data is protected | `apps/api/tests/test_cache_cleanup.py` |
| Privacy telemetry | Sampled numeric aggregates only; arbitrary task text, identifiers and photos are rejected | client telemetry API/web tests |
| 200 users | Harness contract and disposable PostgreSQL capacity benchmark | Historical only: 46 harness tests and 200 sessions/4000 cadence requests passed for source hash `c459…74e0`; current source is UNVERIFIED |

## Historical verification (not current release evidence)

- `./scripts/verify.sh api` — historical run only; current source UNVERIFIED.
- `cd apps/web && npm test` — historical run only; current source UNVERIFIED.
- `cd apps/web && npm run build` — historical run only; current source UNVERIFIED.
- `cd apps/web && npm run check:contrast` — historical run only; current source UNVERIFIED.
- Browser/PWA runs recorded before Task 9 are historical; current scoped smoke is
  reported separately and does not replace the complete web gate.
- The 200-user capacity run is historical; it was not repeated for current source.
