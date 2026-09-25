# Task 15 report — managed system console

## Outcome

- Added the Classic `/system` route for built-in `admin` and `royal` roles.
- Admin sees current/7-day activity, host resources, PostgreSQL/containers/network/Wi‑Fi, queue/worker/Tracker/Tuna, cleanup/storage/backup state, and the existing permission-scoped sync-attention destination. Admin cannot load or submit host operations.
- Royal sees all typed operation kinds from the live capability snapshot. Unavailable and context-dependent kinds remain disabled with a concrete reason. Direct safe operations require an exact phrase plus password and TOTP/recovery reauthorization. No command/argv input exists.
- Polling pauses while hidden, coalesces focus/visibility refreshes, and resumes without duplicate timers. Active operation progress is restored only for the stored operation UUID.
- Capability state, TTL, and revision fail closed. Revision drift refreshes capabilities, clears reauthorization inputs, and never retries/submits automatically.
- Carried forward the Task 14 invariant: every production typed operation schema, API bridge request, inbox record, and resumed consumer request requires a valid current `capability_revision`; legacy/missing/drifted requests cannot consume a grant or execute an effect.

## RED evidence

- Typed API/bridge tests initially accepted requests without `capability_revision`; host schema/resume tests reached the consumer without the required field.
- `SystemPage.test.tsx` initially did not exist; TTL and role/polling/progress contracts were written before the implementation.
- Expired capabilities initially left operation buttons enabled.
- A late 403 initially retained protected system metrics; the new denial test failed until the page cleared protected state.
- Route coverage initially rejected `/system` as missing from the executable Classic route inventory.
- The first system-only route evidence run exposed an incorrect dialog CSS selector (7 passed, 1 failed); the selector was changed to the dialog's actual accessible titled structure and the same 8 cases passed.

## PASS evidence

- `apps/api/.venv/bin/pytest -q apps/api/tests/test_host_capabilities.py apps/api/tests/test_privileged_auth.py apps/api/tests/test_admin_ops.py` — 38 passed (one upstream Starlette deprecation warning).
- `PYTHONPATH=deploy/host:apps/api/src apps/api/.venv/bin/pytest -q tests/host/test_host_commands.py tests/host/test_host_exclusion.py tests/host/test_task4_storage_capabilities.py tests/host/test_restore_retention.py tests/host/test_operation_capabilities.py` — 146 passed.
- `npm test -- --run src/domains/system/SystemPage.test.tsx src/app/routing/routeManifest.test.ts src/app/routing/routeCoverageManifest.test.ts src/app/routing/accessPolicy.test.ts src/app/routing/AppRouter.test.tsx` — 5 files, 682 passed.
- `npx tsc -b --pretty false` — passed.
- `npm run check-nav` — passed, 32 route ids.
- `npx playwright test e2e/operational/admin-system.spec.ts --project=chromium --workers=1` — 2 passed (390px admin and 1440px royal confirmation/resume).
- `npx playwright test e2e/operational/route-state-evidence.spec.ts e2e/operational/route-owner-contracts.spec.ts --project=chromium --workers=1 --grep 'route-coverage:system:'` — 8 passed.

## Deferred by task constraint

- Firefox/WebKit, full E2E, soak/load, and long-running suites were not run.

## Self-review and concerns

- Unsupported capabilities fail before grant consumption and before job/inbox creation; the host consumer independently rejects missing or changed revisions before effects.
- The six safe capabilities are represented. `backup-verify` and `usb-select` remain visibly disabled until an exact object can be selected from a safe discovery result; the current job projection does not expose a typed discovery-result selector. Cleanup execute remains disabled until the host advertises and supplies a supported plan flow.
- No host-mutating endpoint was invented for admin. Safe sync retry stays in the existing task-card workflow where its current permissions are enforced.
- PWA/task caches were not changed.

## Fix round 1 — 2026-09-25

### RED evidence

- Grant revision drift: an issued Task 13 grant had no persisted capability revision, so an A→B capability change could not invalidate the old grant.
- Safe typed confirmations: missing `confirmation` was accepted by the API schema for the six production-safe operations.
- Legacy diagnostics: `POST /admin/ops/diagnostics` still created a fixed-binding host job outside the typed capability contract.
- Protected state: a 401 refresh retained the seven-day chart; only 403 cleared the console.
- Resume: without a local UUID, the page adopted the server's arbitrary current job.
- Partial telemetry: missing Tracker/backup/cleanup values rendered as healthy, and the chart had no tabular equivalent.
- Work attention URL: `sync=needs_attention` was discarded by parse/serialize.
- USB selection: the public host result stripped discovered device metadata and the console had no exact-object selector.
- Host backup context: the production adapter advertised verification without confirming that the protected external recovery key existed.

### PASS evidence

- `uv run pytest -q tests/test_host_capabilities.py tests/test_system_observability.py` (API cwd) — 40 passed.
- `uv run pytest -q tests/test_models_migration.py` (API cwd) — 34 passed.
- `uv run pytest -q tests/test_ops_host_review.py` (API cwd) — 32 passed.
- `uv run pytest -q tests/test_ops_host_bridge.py::test_public_result_projects_only_sanitized_usb_device_selection_metadata tests/test_tracker_read.py::test_tracker_read_filters_attention_state_before_pagination` — 2 passed.
- `env PYTHONPATH=deploy/host uv run --project apps/api pytest -q -x tests/host/test_host_commands.py tests/host/test_operation_capabilities.py tests/host/test_migration_graph.py tests/host/test_release_metadata.py` — 126 passed.
- `npm test -- --run src/domains/system/SystemPage.test.tsx src/domains/work/workUrl.test.ts src/domains/work/IssueWorkbench.test.tsx src/domains/work/workData.test.ts src/components/admin/AdminOpsPanel.test.tsx src/opsApi.test.ts` — 6 files, 192 passed.
- `npm run build` — TypeScript and Vite build passed.
- `npx playwright test e2e/operational/admin-system.spec.ts --project=chromium --workers=1` — 2 passed at 390px/1440px.
- `uv run --project apps/api python scripts/check-release-migrations.py` — release metadata, policy and Alembic agree on `0047_capability_revision`.
- Targeted Ruff passed; targeted Oxlint reported only the pre-existing `Date.now()` render warning and an existing IssueWorkbench dependency warning.

### Fix-round outcome and self-review

- Migration `0047_capability_revision` persists the revision on grants and append-only audit rows. Production typed issue/consume/API/bridge/consumer paths require and exactly compare it. Drift denial is audited, leaves the grant unused, and creates no job or inbox record.
- The legacy diagnostics POST is retired with 410. The Classic admin panel no longer offers it; royal diagnostics uses only the UUID typed endpoint with the live revision and exact server-enforced phrase.
- Built-in admin/royal is now the telemetry API authority; a custom role carrying `nav.admin` is denied directly.
- 401 and 403 immediately clear summary, history, capabilities and job. Resume adopts only the exact UUID stored after a locally accepted operation.
- `sync=needs_attention` round-trips through URL state and is sent to the server. The server filters reliable actions needing attention before pagination; task-card retry retains its existing role checks.
- Missing telemetry renders `Нет данных`/`Неизвестно`; active-user chart values have a compact accessible table.
- USB discovery exposes only bounded UUID/removable/mounted metadata. USB selection accepts only a dropdown UUID from that projection. No path or arbitrary device input is exposed.
- Backup verification requires an exact backup UUID. If the protected external recovery key is absent, the host reports `context_unavailable` and the UI explains why; the key is never accepted into API JSON, inbox, logs, localStorage, or React state. This deliberately uses the existing root-owned recovery-key channel rather than adding an unsafe secret-bearing queue field.
- The ten policy-blocked mutating kinds remain unavailable; cleanup execute remains disabled until an executable host plan capability exists.

### Deferred by task constraint

- Firefox, WebKit, full/long E2E, soak and load suites remain intentionally unrun.

## Fix round 5 — 2026-09-25

### RED evidence

- A UUID was bound only to actor and kind, so the same UUID could be replayed with a different USB device, backup UUID or package payload.
- The single file-backed current-job slot could replace terminal operation A with B before A's sanitized result reached the durable receipt.
- A 15-second exact-status 404 cleared the browser reservation and enabled a new UUID even though a delayed original request could still execute.
- The browser reserved the UUID only after reauthorization, so a crash or reload during a slow second-factor request lost the exact operation identity and safe draft.
- Retention age-deleted `received`/`accepted` rows and pruned only 500 rows per pass, allowing live work loss and leaving large expired sets above the hard ceiling.

### PASS evidence

- `.venv/bin/pytest -q tests/test_host_capabilities.py tests/test_operation_registry.py tests/test_cache_cleanup.py::test_cleanup_once_prunes_live_merge_and_diagnostic_unknowns tests/test_ops_host_bridge.py::test_exact_operation_status_is_actor_bound_and_sanitized` — 39 passed.
- `.venv/bin/pytest -q tests/test_models_migration.py::test_alembic_upgrade_builds_schema tests/test_models_migration.py::test_migrated_schema_matches_models tests/test_models_migration.py::test_migrated_indexes_and_foreign_keys_match_models` — 3 passed.
- `env PYTHONPATH=deploy/host apps/api/.venv/bin/pytest -q tests/host/test_release_metadata.py tests/host/test_migration_graph.py` — 23 passed.
- `apps/api/.venv/bin/python scripts/check-release-migrations.py` — Alembic, release metadata and migration policy agree on `0049_operation_request_digest`.
- `npm test -- --run src/domains/system/SystemPage.test.tsx src/opsApi.test.ts` — 2 files, 20 passed.
- `npm run build` — TypeScript, Vite and service-worker build passed.
- `npx playwright test e2e/operational/admin-system.spec.ts --project=chromium --workers=1` — 2 passed at 390px and 1440px.
- Targeted Ruff, Oxlint and `git diff --check` passed.

### Fix-round outcome and self-review

- Migration `0049_operation_request_digest` stores a SHA-256 digest of the canonical safe typed request. It includes kind, capability revision and every operational argument while excluding UUID identity, confirmations and all credential/grant fields. Exact replay returns the prior receipt; a changed device, backup or generic payload returns 409 before grant consumption or host effects.
- Rejected receipts are immutable idempotency receipts rather than retryable aliases. Accepted/current replay does not consume another grant, and the host's durable dispatch checkpoint remains the one-effect authority for concurrent delayed/retry delivery.
- Current typed receipts are snapshotted before every admin operation replacement and on both sides of reconciliation. A terminal A retains its sanitized result after B replaces the file-backed slot.
- Exact 404 never clears or unlocks the client reservation. The browser keeps only the allowlisted non-secret exact request draft, persists it before reauthorization, and offers a same-UUID retry that requires a fresh password plus TOTP/recovery grant. Passwords, one-time codes, confirmation phrases and authorization tokens never enter browser storage.
- Retention deletes only terminal receipts older than seven days, without a 500-row truncation. `received`, `accepted` and running rows are never age/count-pruned; if 5,000 non-prunable receipts fill the registry, admission fails closed with 503 and no live row is deleted.
- Same-actor re-login and different-actor isolation remain covered. Public result storage remains the existing sanitized projection.

### Deferred by task constraint

- Firefox, WebKit, full/long E2E, soak and load suites remain intentionally unrun.

## Fix round 3 — 2026-09-25

### RED evidence

- The client treated HTTP `ApiError` differently from a lost response and deleted the reserved UUID on a 409, even though the server may already have durably stored the job before dispatch failed.
- Royal polling still read `/admin/ops/job`, an arbitrary current-job snapshot, rather than reconciling the exact client-generated UUID.
- When POST never reached the server, an ambiguous local reservation had no authoritative absence query or reconnect recovery path.
- No exact operation-status route existed; the new royal/session-bound API test returned 404 before implementation.

### PASS evidence

- `uv run pytest -q tests/test_ops_host_bridge.py` — 59 passed.
- `npm test -- --run src/domains/system/SystemPage.test.tsx src/opsApi.test.ts` — 2 files, 18 passed.
- `uv run ruff check src/robopark_api/routers/admin_ops.py tests/test_ops_host_bridge.py` — passed.
- Targeted `npx oxlint` for System/client/E2E files — passed without warnings.
- `npm run build` — TypeScript, Vite, and service-worker build passed.
- `npx playwright test e2e/operational/admin-system.spec.ts --project=chromium --workers=1` — 2 passed at 390px/1440px.
- `git diff --check` — passed.

### Fix-round outcome and self-review

- `GET /admin/ops/operations/{operation_id}` is royal-only and requires the exact UUID, original actor, and original authenticated session hash. It returns only id/kind/state/phase/stable error, sanitized host result, and bounded progress; raw logs, artifacts, timestamps, request envelopes, and arbitrary current job IDs are excluded. Found/denied lookups are audited without echoing the requested UUID.
- The console never calls the arbitrary current-job endpoint. It reads operation status only when an exact UUID exists in local storage.
- UUID reservation is durable before POST. Every POST failure, regardless of HTTP/network exception type, enters the same visible `Проверяем получение запроса` state and retains the lock.
- Exact match resumes the one job. Unknown connectivity preserves the reservation and blocks a second UUID/effect. Authoritative exact 404 clears the reservation, renders `Запрос не получен`, and unlocks a fresh reauthorization attempt.
- Reload starts from the stored UUID in a visible reconciling state and cannot adopt any other server job. No password, TOTP/recovery code, authorization token, confirmation, or host context is persisted.
- No PWA/task caches, migrations, or release metadata were changed in this round.

### Deferred by task constraint

- Firefox, WebKit, full/long E2E, soak and load suites remain intentionally unrun.

## Fix round 2 — 2026-09-25

### RED evidence

- A live capability refresh from revision A to B left an already-filled confirmation dialog open; the regression observed the old confirmation, password, and TOTP values still present.
- A lost response from the operation POST left no durable client UUID because local storage was written only after the response; reload could not resume the exact request safely.
- A one-second capability TTL stayed visually enabled until another render or the 30-second poll.
- The system-console sync projection counted three mixed `schedule_park`, `task_control`, and `tracker_issue` actions while its destination listed only the one actionable Tracker issue.
- Capability drift injected after second-factor verification still returned a successful stale grant because the live snapshot was checked only before credential verification.

### PASS evidence

- `uv run pytest -q tests/test_host_capabilities.py tests/test_system_observability.py tests/test_admin_health.py tests/test_tracker_read.py::test_tracker_read_filters_attention_state_before_pagination` — 45 passed.
- `npm test -- --run src/domains/system/SystemPage.test.tsx src/opsApi.test.ts src/domains/work/workUrl.test.ts src/domains/work/IssueWorkbench.test.tsx` — 4 files, 172 passed.
- `uv run ruff check` on the six changed API/test files — passed.
- `npx oxlint` on the three changed System console files — passed without warnings.
- `npm run build` — TypeScript, Vite, and service-worker build passed.
- `npx playwright test e2e/operational/admin-system.spec.ts --project=chromium --workers=1` — 2 passed at 390px/1440px.
- `git diff --check` — passed.

### Fix-round outcome and self-review

- `SystemOperations` is keyed by the capability revision, so any A→B refresh synchronously destroys the old dialog state before the new revision can be used.
- Capability freshness is driven by a bounded `expires_at` timer with cleanup on remount/unmount; no clock read occurs during render.
- The client UUID is reserved in local storage before POST. A definite HTTP rejection clears it; an ambiguous transport/lost-response keeps it, closes the dialog, blocks a second start, and resumes only the exact UUID.
- The console requests a Tracker-only sync projection, matching the `/work?sync=needs_attention` destination. General `/admin/health` and metric collection retain their all-resource projection.
- Grant issuance repeats the live capability/revision check after credentials are verified and directly before grant insertion. Drift is committed as a denial audit and no grant is inserted.
- No PWA/task caches or release metadata were changed in this round.

### Deferred by task constraint

- Firefox, WebKit, full/long E2E, soak and load suites remain intentionally unrun.

## Fix round 4 — 2026-09-25

### RED evidence

- Exact operation status lived only in the single file-backed host job slot, so a newer job erased the only recovery authority for an older client UUID.
- Status ownership was bound to a session hash, preventing the same royal actor from resuming after logout/login.
- A focus/visibility refresh could query an exact UUID while its POST promise was still unresolved and treat the early 404 as authoritative absence.
- A never-reached POST and an accepted-but-replaced operation were indistinguishable after reload; no bounded durable receipt/history existed.

### PASS evidence

- `.venv/bin/pytest -q tests/test_operation_registry.py` — 4 passed.
- `.venv/bin/pytest -q tests/test_host_capabilities.py` — 27 passed.
- `.venv/bin/pytest -q tests/test_ops_host_bridge.py` — 59 passed.
- `.venv/bin/pytest -q tests/test_models_migration.py::test_alembic_upgrade_builds_schema tests/test_models_migration.py::test_migrated_schema_matches_models tests/test_models_migration.py::test_migrated_indexes_and_foreign_keys_match_models` — 3 passed.
- `npm test -- --run src/domains/system/SystemPage.test.tsx src/opsApi.test.ts` — 2 files, 19 passed.
- `npm run build` — TypeScript, Vite, and service-worker build passed.
- `env PYTHONPATH=deploy/host apps/api/.venv/bin/pytest -q tests/host/test_release_metadata.py tests/host/test_migration_graph.py` — 23 passed.
- `apps/api/.venv/bin/python scripts/check-release-migrations.py` — Alembic and release declarations agree on `0048_host_operation_status`.
- Targeted Ruff, Oxlint, and `git diff --check` passed.

### Fix-round outcome and self-review

- Migration `0048_host_operation_status` adds an actor-bound durable receipt keyed by the exact client UUID. It records only kind, received/accepted/terminal state, bounded phase/error/progress, timestamps, and the already-sanitized host result. Session credentials, confirmations, authorization tokens, paths, logs, and raw envelopes are never stored.
- The receipt is committed before bridge/capability/dispatch work and updated after acceptance, rejection, host reconciliation, and terminal completion. Exact reads bind to the original actor, not the login session; another actor receives the same non-enumerating 404 as an unknown UUID. Re-login and replacement of the host current-job slot no longer erase status.
- Registry cleanup is integrated into the existing cache-cleanup lease: seven-day expiry, 500-row batches, and a hard 5,000-row admission ceiling prevent unbounded database/host growth without deleting a live receipt merely to make room.
- The browser stores only `{id, kind, created_at, phase}` before POST. Focus/timer reconciliation is suppressed while that POST promise is unresolved. Reload/offline paths retain and lock the UUID; exact absence is accepted only after a bounded 15-second grace, while received/accepted/terminal receipts are authoritative immediately.
- Replays of an accepted UUID return its existing receipt without consuming another grant or dispatching another effect. A rejected, never-accepted UUID may be retried safely with the same idempotency identity.

### Deferred by task constraint

- Firefox, WebKit, full/long E2E, soak and load suites remain intentionally unrun.
