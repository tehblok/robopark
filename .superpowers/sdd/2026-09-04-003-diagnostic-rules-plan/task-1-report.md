# Task 1 — Persisted diagnostic rule model

Status: COMPLETE. Implemented from base `848e9f2` in commit `671db5d`
(`feat(api): add diagnostic rule model`). No matcher, route, service, or UI code was added.

## Persisted contract

- Added Alembic revision `0019_diagnostic_rules` after the verified previous head
  `0018_analytics_observations`.
- Added global `DiagnosticRule` with exactly the requested persisted fields and no timestamps.
- Required marker coordinates are finite normalized floats in `[0, 1]`; markerless unknown events
  are a normalized-event concern and are not stored as editor rules in this task.
- Enum-like values are enforced both by Pydantic and database CHECK constraints:
  `exact|regex`, `info|warning|critical`,
  `top|front|rear|left|right|isometric`, and `point|outline|zone`.
- Rule identity is unique on `(source_path, match_kind, pattern)`. The same pattern remains valid
  for another source path or match kind.
- `(sort_order, id)` is indexed so callers can use `id` as the deterministic tie-breaker without
  forcing globally unique sort positions. Database defaults are `is_enabled=true`, `sort_order=0`.
- Create requires every semantic rule/marker field while defaulting only enablement/order. Update is
  partial but rejects explicit null for every non-null persisted field. Output supports ORM objects.

## RED / GREEN evidence

Initial focused command:

```text
cd apps/api
.venv/bin/python -m pytest -q tests/test_models_migration.py tests/test_diagnostic_rules.py
53 failed, 10 passed
```

Expected RED: metadata lacked `diagnostic_rules`, Alembic still ended at 0018, revision 0019 could
not be resolved, and `DiagnosticRule` / its Pydantic schemas did not exist.

Final focused command:

```text
cd apps/api
.venv/bin/python -m pytest -q tests/test_models_migration.py tests/test_diagnostic_rules.py
63 passed, 1 external Starlette/httpx deprecation warning
```

The tests cover allowed values, non-finite/out-of-range coordinates, omitted/explicit-null create
fields, explicit-null partial updates, ORM output, non-null/default metadata, duplicate identity,
pattern reuse across path/kind, and database enforcement rather than mock behavior.

## Migration evidence

A fresh temporary SQLite database was upgraded through 0018 and then with real Alembic CLI to
head. `alembic current` and `alembic heads` both reported `0019_diagnostic_rules (head)`.
The migration test verifies the exact columns, all columns non-null, the named composite unique
constraint, `(sort_order, id)` index, all six named CHECK constraints, downgrade to 0018, and
metadata/autogenerate parity. No application database was touched.

## Verification

- Focused migration/schema/DB subset: `52 passed`.
- Ruff over all API source, tests, and migrations: `All checks passed!`.
- Ruff format check over all API source, tests, and migrations: `184 files already formatted`.
- `git diff --check`: exit 0.
- Full API suite: `818 passed, 1 failed`, with the sole failure at the unchanged baseline test
  `test_symlink_loop_is_normalized_at_direct_and_validation_boundaries` asserting Python exception
  context internals. The service and test have no diff from base, and the failure reproduces alone
  under this worktree's Python 3.13 runtime.
- Full API suite excluding that independently reproduced baseline assertion:
  `818 passed, 1 deselected, 1 warning in 80.65s`.

## Self-review

Checked revision lineage, clean metadata creation, SQLite behavior, migrated constraint/index
reflection, required-null/update semantics, finite coordinate handling, and uniqueness behavior.
The change remains limited to the Task 1 migration, ORM model, schemas, and their tests.
