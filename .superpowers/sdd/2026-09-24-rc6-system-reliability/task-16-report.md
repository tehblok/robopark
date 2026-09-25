# Task 16 report — pre-merge blockers and bounded gates

## Scope and outcome

- This report covers only the mandatory pre-merge blockers and short release gates.
- No merge to `main`, signing, archive/package creation, installation, deployment,
  host mutation, soak, load test, or broad/full suite was performed.
- Release declarations remain `0.2.0-rc.6`, channel `rc`. The independent
  reliability remediation below advances the linear migration head to
  `0050_media_action_dependency`.

## Mandatory blocker 1 — exact unknown UUID survives failed retry authentication

Commit: `269cced7` (`fix(system): retain unknown operation retry identity`).

### RED

`npm test -- --run src/domains/system/SystemPage.test.tsx` failed the new regression:
after an ambiguous POST, exact 404, same-UUID retry, and invalid TOTP,
`readOperationReservation()?.id` was `undefined` instead of the original UUID.

### Fix and invariant

- A first authorization failure before any unknown POST still clears its provisional
  reservation.
- Once a safe draft belongs to an unknown request, failed password/TOTP
  reauthorization leaves the reservation and allow-listed draft intact.
- Correcting credentials in the still-open dialog reuses the same UUID and payload.
- The regression observes all three reauthorization attempts and both POST attempts
  using the same UUID; no password, TOTP, confirmation, or token is stored.
- Server/host receipt and dispatch idempotency remain the one-effect authority if
  delayed original and retry delivery overlap.

### PASS

`npm test -- --run src/domains/system/SystemPage.test.tsx` — 20 passed.

## Mandatory blocker 2 — concurrency-strict 5,000 receipt admission

Commit: `7ebe95b8` (`fix(ops): serialize receipt capacity admission`).

### RED

The new admission contract failed because `operation_registry` had no dedicated
database idempotency/advisory lock. The pre-fix implementation performed
prune/count/insert as independent operations, so concurrent workers could both
observe the last free slot.

### Fix and invariant

- Existing exact-UUID replay, safe terminal pruning, count, insert, commit and refresh
  execute under the single `host-operation-registry-admission` lock.
- PostgreSQL uses the existing bounded independent lock pool and
  `pg_try_advisory_xact_lock`; SQLite/test fallback uses the existing keyed process
  lock plus bounded `flock`. Unrelated tables and unrelated workflow lock keys are
  not serialized.
- The controlled regression preloads exactly 4,999 accepted/live receipts and starts
  two concurrent admissions. Exactly one is accepted, one receives the full-registry
  result (mapped by the route to HTTP 503), and the final count is exactly 5,000.
- `received`, `accepted`, running and otherwise live rows remain non-prunable.

### PASS

- `uv run --frozen --extra dev python -m pytest -p no:cacheprovider -q tests/test_operation_registry.py tests/test_database_locks.py` — 23 passed.
- Targeted Ruff check and format check for both changed API files — passed.

## Bounded release gates

- API/security/migration:
  `uv run --frozen --extra dev python -m pytest -p no:cacheprovider -q tests/test_operation_registry.py tests/test_database_locks.py tests/test_privileged_auth.py tests/test_admin_ops.py tests/test_models_migration.py`
  — 72 passed; one upstream Starlette deprecation warning.
- Web unit:
  `npm test -- --run src/domains/system/SystemPage.test.tsx src/opsApi.test.ts src/app/routing/AppRouter.test.tsx`
  — 3 files, 58 passed.
- Web production build: `npm run build` — passed (TypeScript, Vite, service worker).
- Host/release:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=deploy/host:apps/api/src uv run --project apps/api --frozen --extra dev python -m pytest -p no:cacheprovider -q tests/host/test_operation_capabilities.py tests/host/test_host_commands.py tests/host/test_release_metadata.py tests/host/test_release_version.py tests/host/test_migration_graph.py`
  — 132 passed.
- Release migration checker:
  `uv run --project apps/api --frozen --extra dev python scripts/check-release-migrations.py`
  — `Release migration heads agree.`
- Release version checker:
  `uv run --project apps/api --frozen --extra dev python scripts/check-release-version.py --tag v0.2.0-rc.6`
  — `0.2.0-rc.6`.
- Release notes checker:
  `uv run --project apps/api --frozen --extra dev python scripts/generate-release-notes.py --check`
  — current.
- Packaging/version source contracts:
  `pytest tests/host/test_packaging.py -k 'version_sources_match_authoritative_version or release_tag_consistency or repository_candidate_is_clean_and_prerelease_only'`
  — 6 passed, 121 deselected.
- Web lint: `npm run lint` — exit 0 with 16 pre-existing warnings outside the
  changed System files (Fast Refresh and hook dependency warnings).
- Navigation consistency: `npm run check-nav` — 32 route ids, passed.
- Chromium critical console journeys:
  `npx playwright test e2e/operational/admin-system.spec.ts --project=chromium --workers=1`
  — 2 passed (390px admin and 1440px royal operation/resume).
- `git diff --check b42775597d9ba95eb1c8b1f69edd1584aa0562ee..HEAD` — passed.

## Explicitly deferred / residual release boundaries

- Firefox and WebKit were not installed in the local Playwright cache. They were not
  downloaded and no browser matrix was started.
- Full API/web/host suites, PostgreSQL container suite, installer scenarios, Docker
  builds, soak and 200-user load remain unrun by the explicit no-long-tests boundary.
- The standalone artifact verifier requires a built archive. Archive creation and
  offline verification remain after independent whole-branch review and the explicit
  merge/package boundary.
- The existing 16 web lint warnings remain outside this isolated task; there are no
  lint errors and none points at the changed System operation files.

## Independent reliability remediation — client replay, media retention and cleanup

Commit: `3f0c01b6` (`fix(sync): retain interrupted actions and media`).

### Fixed invariants

- A restarted client atomically reconciles orphan `sending` actions and `uploading`
  media to retryable state under the existing cross-tab coordinator. Action UUID,
  idempotency key, media UUID and payload identity remain unchanged.
- Confirmed review media is retained on the client while its durable action is
  nonterminal. On the server migration `0050_media_action_dependency` binds the
  completed upload to the exact actor/device/action and starts the seven-day cleanup
  clock only after a terminal server result. Tracker `attention` responses stay
  replayable without losing the photo.
- System-operation reservations are keyed by immutable account id, included in the
  storage inventory, purged on logout/auth transition, and cannot expose one royal's
  draft or operation UUID to another account in the same browser.
- Attachment and resumable-upload cleanup unlinks first. A transient filesystem
  failure retains retryable metadata; a later cleanup removes both file and row.

### Bounded PASS evidence

- Web reliability/auth tests: seven files, 114 passed.
- API media/offline/cleanup/migration selection: 53 passed, 28 deselected; one
  upstream Starlette deprecation warning.
- Model/migration contract selection: 3 passed, 31 deselected.
- Host migration/release metadata selection: 23 passed, 97 deselected.
- Release migration checker: `Release migration heads agree.`
- API Ruff check: passed. Web lint: exit 0 with the same 16 unrelated warnings.
- `git diff --check`: passed.

The production web build passed after the independent admin lane corrected its owned
test typing; this lane did not edit or stage those files. No long/full/soak/load test
was run.

## Independent reliability remediation — database locks and legacy operations

### Fixed invariants

- Nested PostgreSQL idempotency scopes in one workflow reuse the active dedicated
  connection and transaction. A claim/handoff dispatched from offline sync no longer
  waits for a second slot while holding the first advisory lock.
- The lock-only pool is bounded at 16 instead of imposing a global two-workflow
  ceiling. Advisory keys still serialize the same idempotency action; unrelated keys
  proceed independently. SQLite keeps its keyed process and bounded `flock` fallback.
- Legacy mutation endpoints for abort, snapshot, restore, archive/GitHub update,
  diagnostics and repair are retired with HTTP 410 and cannot enqueue host work.
  Destructive actions remain reachable only through `/admin/ops/operations`, whose
  contract requires a typed UUID, current capability revision and privileged grant.
- The legacy admin panel and API client no longer expose those mutation controls. It
  retains read-only health/version/update status, operation progress and download of
  an already completed diagnostic artifact.

### Bounded PASS evidence

- Database lock regressions: 12 passed, including two concurrent nested workflows and
  four unrelated concurrent keys.
- Legacy route/capability regressions: 28 passed; enqueue helpers were patched to fail
  the test if any retired route reached them.
- Operations HTTP/restore/review/security regressions: 53 passed.
- Privileged-auth regressions: 13 passed.
- Claim permission selection: 6 passed, 105 deselected.
- Web admin/health/typed-gateway tests: 3 files, 14 passed.
- Targeted API Ruff check: passed. Web production build: passed.

No full suite, Docker run, soak or load test was run. The existing Python files are
not globally Ruff-format-clean; the bounded lint check for the touched files has no
errors, and `git diff --check` is the whitespace gate for this remediation.

## Independent media reliability remediation — close the bind gap

Commit: `941ae79a` (`fix(sync): bind review media before upload`).

### Fixed invariants

- The review UUID is allocated before upload. IndexedDB writes the review action and
  media record atomically, so a close/reload cannot leave an acknowledged upload
  without its durable action identity.
- Upload creation carries the exact action UUID and sync device id. The authenticated
  actor remains server-derived; the server validates both client identifiers and
  persists the dependency before the first chunk. Exact retry is idempotent and a
  different dependency is rejected.
- A legacy confirmed upload that cleanup already removed no longer strands the
  review. `media_dependency_pending`/missing resets the retained local blob and the
  same action to retryable state, recreates the same media identity, and retries the
  same action UUID/idempotency key without a duplicate Tracker effect.
- `conflict` and `attention` are nonterminal on client and server. Neither produces a
  terminal media acknowledgement or durable conflict receipt. Bound media survives
  beyond seven days until conflict resolution; only a confirmed action starts the
  terminal seven-day retention clock.
- No schema change was necessary; release migration head remains
  `0050_media_action_dependency`.

### Bounded PASS evidence

- Web atomic/reupload/retention/workbench tests: four files, 165 passed.
- Web production build: passed. Web lint: exit 0 with the same 16 unrelated warnings.
- API media/offline/migration selection: 28 passed, 28 deselected; one upstream
  Starlette deprecation warning.
- Model/migration selection: 3 passed, 31 deselected.
- Release migration checker: `Release migration heads agree.`
- Targeted Ruff check and format check: passed. `git diff --check`: passed.

No full suite, browser matrix, Docker, soak or load test was run.

## Pre-merge state

- Base reviewed HEAD: `b42775597d9ba95eb1c8b1f69edd1584aa0562ee`.
- Blocker commits: `269cced7`, `7ebe95b8`.
- No uncommitted product-code changes were present before this report was added.
- Stop point: independent whole-branch review; do not merge, sign, package, install,
  deploy, soak or load from this report.

## Independent media reliability remediation — recover missing completed blobs

Commit: `507638de` (`fix(sync): recover missing completed media`).

### Fixed invariants

- Upload creation now returns an explicit `active`, `reinitialized` or `completed`
  status. A completed database row whose expected blob disappeared is reopened with
  the same upload/media/action identity; an intact completed row remains an
  idempotent no-op.
- Reopening requires an exact authenticated actor, device id, action UUID, issue,
  filename, MIME type, size and digest match. Dependency, payload or device mismatch
  remains a conflict.
- The client skips transfer only for an explicit `completed` server status. A
  `reinitialized` response uploads the retained local blob again, even if stale
  completion fields are present.
- Offline sync recognizes the server's actual `media_upload_missing` conflict and
  resets both retained media and the same `submit_review` action to retryable state.
  Media UUID, action UUID and idempotency key are preserved.
- The integration regression simulates an unlink-before-database-state mismatch:
  first review submission reports missing media, the same upload id is restored,
  and receipt replay proves the action effect is applied once.
- No schema change was necessary; release migration head remains
  `0050_media_action_dependency`.

### Bounded PASS evidence

- API media/offline-sync modules: 25 passed; one upstream Starlette deprecation
  warning.
- Focused exact-identity/reopen/integration selection: 3 passed.
- Web resumable-upload and sync-engine modules: 32 passed.
- Web production build: passed. Web lint: exit 0 with only pre-existing unrelated
  warnings.
- Targeted API Ruff format/check: passed.
- Release migration checker: `Release migration heads agree.`
- `git diff --check`: passed.

No full suite, browser matrix, Docker, soak or load test was run.
