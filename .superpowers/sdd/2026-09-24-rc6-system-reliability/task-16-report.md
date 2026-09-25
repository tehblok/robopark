# Task 16 report — pre-merge blockers and bounded gates

## Scope and outcome

- This report covers only the mandatory pre-merge blockers and short release gates.
- No merge to `main`, signing, archive/package creation, installation, deployment,
  host mutation, soak, load test, or broad/full suite was performed.
- Release declarations remain `0.2.0-rc.6`, channel `rc`, migration head
  `0049_operation_request_digest`; no migration was needed.

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

## Pre-merge state

- Base reviewed HEAD: `b42775597d9ba95eb1c8b1f69edd1584aa0562ee`.
- Blocker commits: `269cced7`, `7ebe95b8`.
- No uncommitted product-code changes were present before this report was added.
- Stop point: independent whole-branch review; do not merge, sign, package, install,
  deploy, soak or load from this report.
