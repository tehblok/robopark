# Task 4 Report: Renderer uses DB config

## Status

Implemented the Emergency renderer's database-backed section configuration.

## Changes

- `list_sections(db, role)` delegates to role-filtered DB configuration.
- `render_section(db, payload, section_id)` reads DB config and rejects missing or disabled sections.
- Added `fields_and_top_level_leftovers` rendering for `service_raw`.
- Preserved the existing dig, scalar, nested-value, and error formatting helpers.
- Updated the mechanic Emergency router and affected tests for the required DB session.

## TDD and verification

- RED: renderer tests failed with the old no-DB function signatures (4 expected failures).
- GREEN: focused renderer/config tests passed (12 passed).
- Regression: full API suite passed (139 passed, 1 upstream Starlette deprecation warning).
- IDE diagnostics reported no linter errors. Ruff is not installed in the API environment, so `uv run ruff check src tests` could not run.

## Concerns

- `get_section_config` now returns `None` for disabled sections so callers cannot render them.
- Production startup seeding is outside Task 4; the integration test seeds the DB config explicitly.
