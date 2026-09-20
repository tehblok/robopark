# SDD ledger — plan: docs/superpowers/plans/2026-09-20-pwa-offline-first.md

Baseline: linked worktree `codex/product-completion`; resource cache tests 35 passed.
Pre-flight: Task 1 OfflineDb -> Task 3 SyncEngine: scoped generation and atomic action/media stores align.
Pre-flight: Task 2 batch API -> Task 3 SyncEngine: client action UUID is the stable replay boundary.
Pre-flight: Task 3 SyncEngine -> Tasks 4-7: one provider owns delivery; feature screens enqueue only.
Pre-flight: Task 5 media -> Task 4 review action: media completion precedes comment/review dependencies.
Pre-flight: Task 6 Service Worker -> Tasks 3/5: CacheStorage owns shell only; IndexedDB owns protected work.
Pre-flight: Task 7 schedule/push -> Task 2 revisions: compact push wakes delta sync; it does not carry entities.
Pre-flight: Task 8 cleanup -> Tasks 1/2/5/7: pending states are retention-protected across all stores.
Task 2: Ruling: migration registry tests hard-code every table and Alembic head — update `test_models_migration.py` to include `offline_sync_receipts` and head `0033_offline_sync_receipts`; otherwise the valid migration cannot pass the repository contract.
Task 2: Ruling: release metadata and PostgreSQL acceptance pin the reviewed Alembic head — advance them to `0033_offline_sync_receipts` and add `0032_operator_inv_readonly` as a reversible source head; do not weaken packaging validation.
Task 3: Ruling: `SyncProvider` needs the selected park, which exists only below `ParkProvider`; mount it in `AppRouter.ShellBoundary` instead of `App.tsx` so one engine follows the authenticated park scope without duplicating providers.
Task 4: Ruling: queue comments, handoff and inventory now; create the causal review action builder now, but connect review/photo delivery only with Task 5 resumable media. The server intentionally returns `media_dependency_pending` until that contract exists, so claiming an offline review success earlier would be false.
Task 1: complete (commits 8fe3c03..dd452fa, tests: sh -lc 'cd apps/web && npm test -- src/pwa/offlineDb.test.ts src/lib/deviceResourceCache.test.ts src/lib/resource.test.ts' →    Duration  895ms (transform 126ms, setup 271ms, import 152ms, tests 287ms, environment 1.21s))
Task 2: complete (commits dd452fa..1482d25, tests: sh -lc 'cd apps/api && uv run pytest tests/test_offline_sync.py tests/test_task_lifecycle.py tests/test_inventory.py tests/test_models_migration.py -q' → 70 passed, 1 warning in 23.31s)
Task 3: complete (commits 1482d25..7091222, tests: sh -lc 'cd apps/web && npm test -- src/pwa/syncEngine.test.ts src/pwa/syncCoordinator.test.ts src/pwa/SyncProvider.test.tsx src/api.test.ts' →    Duration  828ms (transform 202ms, setup 421ms, import 223ms, tests 231ms, environment 1.69s))
Task 4: complete (commits 7091222..32e6703, tests: sh -lc 'cd apps/web && npm test -- src/domains/work src/domains/inventory/TaskPartsPanel.test.tsx' →    Duration  4.92s (transform 2.11s, setup 1.81s, import 3.47s, tests 4.91s, environment 7.69s))
Task 5: Ruling: completed upload blobs remain replayable for seven days, then the existing hourly cleanup removes both the database session and staging file; incomplete sessions expire after 24 hours and pending client media remain protected in IndexedDB.
Task 5: complete (commits 32e6703..2faa60f, tests: API focused 95 passed; web media/work/scanner 138 passed; production build passed and emitted a dedicated media worker chunk.)
Task 6: complete (commit d454fd2; service-worker 3 passed, shell/share/sync-center focused 60 passed, PWA Playwright 3 passed on 390px and 1440px.)
Task 7: complete (commit af99d6e; API schedule/push/migration 47 passed, web schedule/notification/routing 27 passed; royal assignment uses park-scoped employee selection rather than numeric ids.)
Task 8: complete pending final commit (API verify 1990 passed / 8 skipped; web 2197 passed; service-worker tests, production build, contrast and PWA Playwright passed; capacity benchmark passed with 200 sessions, 4000 cadence requests, p95 167.64 ms, no 5xx/timeouts/leaks/duplicates.)
Final review: transient sync/media failures use bounded backoff; permanent media errors require attention; account changes purge local work and shared-photo drafts; service-worker activation reloads only after an explicit safe update; schedules are Moscow-time and bounded without N+1 queries; Web Push uses encrypted subscriptions, compact payloads and explicit permission; report/review/return/operator-comment scenarios emit role-scoped events.
