# Offline-first PWA acceptance matrix

Evidence is recorded only after the named command exits successfully. The release does not require an unattended soak.

| Area | Acceptance | Evidence |
|---|---|---|
| Scoped local data | Account/role/permission/park isolation; pending actions survive restart; revoked scope is purged | `apps/web/src/pwa/offlineDb.test.ts`, `deviceResourceCache.test.ts` |
| Idempotent sync | Duplicate delivery cannot repeat a transition or stock write-off; causal dependencies are stable | `apps/api/tests/test_offline_sync.py`, `test_sync_load.py` |
| Single client engine | One leader across tabs; bounded weak-link batches; listeners and requests are released | `apps/web/src/pwa/syncCoordinator.test.ts`, `syncEngine.test.ts`, `performanceBudget.test.ts` |
| Offline work cycle | Comment, handoff, stock write-off, defect code, one photo and review queue without page reload | work/inventory Vitest suites; reconnect path in `pwa-offline.spec.ts` |
| Camera and media | Camera/file fallback, local decoder, worker image preparation, resumable checksummed upload, bounded cleanup | media/scanner tests; API media upload tests |
| Installable shell | Warm navigation works offline; no authenticated API response enters CacheStorage; safe worker activation | service worker build tests, registration tests, `pwa-offline.spec.ts` |
| Schedules | Self-service shifts/leave/sick; admin scoped read; royal edit, copy and bulk assignment | API and web schedule tests |
| Notifications | Role/event matrix, on-shift targeting, internal fallback and explicit permission request | API push and web notification tests |
| Host resources | Expired receipts/uploads and confirmed temporary files are deleted in bounded batches; pending data is protected | `apps/api/tests/test_cache_cleanup.py` |
| Privacy telemetry | Sampled numeric aggregates only; arbitrary task text, identifiers and photos are rejected | client telemetry API/web tests |
| 200 users | Harness contract and disposable PostgreSQL capacity benchmark | 46 harness tests passed; 200 sessions/4000 cadence requests passed, p95 167.64 ms, no 5xx/timeouts/leaks/duplicates (`/private/tmp/robopark-capacity-pwa.json`) |

## Final verification

- `./scripts/verify.sh api` — 1990 passed, 8 skipped.
- `cd apps/web && npm test` — 2197 passed.
- `cd apps/web && npm run build` — production build passed.
- `cd apps/web && npm run check:contrast` — both themes passed.
- `cd apps/web && npx playwright test e2e/pwa-offline.spec.ts --project=chromium` — 3 passed.
- `apps/api/.venv/bin/python scripts/capacity_benchmark.py --users 200 --duration 60 ...` — passed all acceptance gates.
