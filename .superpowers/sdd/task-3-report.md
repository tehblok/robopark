# Task 3 Report: Payload cache

## Implementation

- Added `emergency_cache.get_robot_payload` with an exact 5.0-second
  monotonic TTL.
- Added per-VIN single-flight coordination using one process-wide lock and
  per-flight events. Concurrent requests for different VINs remain independent.
- Added `invalidate_vin` and `clear_cache_for_tests`.
- Successful upstream loads mark the Emergency cookie valid and update the
  keepalive ring.
- `EmergencyAuthError` invalidates the VIN, marks the cookie invalid, and is
  re-raised.
- Added JSON-backed keepalive ring helpers under
  `emergency_keepalive_ring`; touching a VIN moves it to the end and retains at
  most 20 VINs.

## TDD evidence

The new test module was run before implementation and failed during collection
because `robopark_api.services.emergency_cache` did not exist. After
implementation, the focused cache and platform settings suite passed:

```text
10 passed, 1 warning in 0.54s
```

Coverage includes cache reuse, expiry at exactly 5.0 seconds, same-VIN
single-flight, independent VIN flights, auth invalidation, cookie validity,
ring updates, move-to-end behavior, and the 20-item ring limit.

## Verification

- IDE lint diagnostics: no errors in the three changed code/test files.
- Full API suite: `134 passed, 1 failed, 1 warning in 10.18s`.
- The one full-suite failure is pre-existing phase work:
  `tests/test_models_migration.py::test_metadata_has_required_tables` still
  expects only the Phase 1 tables, while earlier Phase 6 commits added
  `emergency_sections`, `emergency_section_roles`, and `emergency_fields`.
  Task 3 does not modify model metadata or that assertion.
# Task 3 Report
Status: Complete
Commit: `feat(api): extend /auth/me and add admin/operator deps`
Implemented: `ParkOut`; extended `UserOut`; `/auth/me` park lookup; admin and approved-operator dependencies.
Tests: Added extended `/auth/me`, assigned-park, and role/access dependency coverage.
Fixtures: Added `login_as` and `seed_pending_operator`.
TDD RED: Auth tests failed because role dependencies were missing.
Verification: `uv run pytest -q` — 47 passed, 1 upstream Starlette/httpx deprecation warning.
