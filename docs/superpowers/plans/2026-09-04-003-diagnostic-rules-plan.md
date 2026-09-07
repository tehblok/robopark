# Diagnostic Rules and Robot Photo Indication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn raw Emergency errors into configurable diagnostic events that select a robot photo view and highlight the affected part.

**Architecture:** Matching and normalization live on the backend. The client receives display-ready events and renders them with existing robot photos; an admin editor writes validated global rules and places markers directly on the selected image.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, Pydantic, React 19, TypeScript, Vitest, pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-04-operations-interface-redesign-design.md`

## Global Constraints

- Rules are global across parks in this release.
- Unknown raw errors remain visible and are never assigned a guessed part.
- Coordinates are normalized floats from 0 through 1.
- Allowed views are `top`, `front`, `rear`, `left`, `right`, `isometric`.
- Allowed indicators are `point`, `outline`, `zone`; severity controls color and no marker blinks.

---

### Task 1: Persisted diagnostic rule model

**Files:**
- Create: `apps/api/alembic/versions/0019_diagnostic_rules.py`
- Modify: `apps/api/src/robopark_api/models.py`
- Modify: `apps/api/src/robopark_api/schemas.py`
- Test: `apps/api/tests/test_models_migration.py`
- Test: `apps/api/tests/test_diagnostic_rules.py`

**Interfaces:**
- Produces: `DiagnosticRule` fields `id, source_path, match_kind, pattern, example, title, description, severity, part, preferred_view, x, y, indicator, is_enabled, sort_order`.

- [ ] Add failing migration and schema tests for enum-like validation, unique rule identity, and coordinate bounds.
- [ ] Run the two pytest files and confirm the table/model is absent.
- [ ] Add migration, SQLAlchemy model, and Pydantic create/update/output schemas with explicit null rejection for required fields.
- [ ] Re-run tests and commit with `git commit -m "feat(api): add diagnostic rule model"`.

### Task 2: Matcher and normalized events

**Files:**
- Create: `apps/api/src/robopark_api/services/diagnostic_rules.py`
- Modify: `apps/api/src/robopark_api/services/emergency_snapshot.py`
- Modify: `apps/api/src/robopark_api/schemas.py`
- Test: `apps/api/tests/test_diagnostic_rules.py`
- Test: `apps/api/tests/test_emergency_snapshot.py`

**Interfaces:**
- Produces: `match_diagnostic_events(db, payload) -> list[DiagnosticEvent]`.
- Extends: `EmergencySnapshotOut.diagnostic_events` with raw value, title, description, severity, part, view, coordinates, and indicator.

- [ ] Add failing tests for exact and regex matching, stable severity/order sorting, disabled rules, invalid paths, and unknown-event fallback.
- [ ] Run focused pytest and confirm normalized events are missing.
- [ ] Implement safe dotted-path lookup and prevalidated regular expressions; never evaluate user code.
- [ ] Integrate matching into snapshot construction and retain the existing `error_banner` compatibility field.
- [ ] Run tests and commit with `git commit -m "feat(api): normalize Emergency diagnostic events"`.

### Task 3: Rule CRUD and preview API

**Files:**
- Create: `apps/api/src/robopark_api/routers/admin_diagnostic_rules.py`
- Modify: `apps/api/src/robopark_api/main.py`
- Modify: `apps/api/src/robopark_api/services/audit.py`
- Test: `apps/api/tests/test_admin_diagnostic_rules.py`

**Interfaces:**
- Produces: list/create/update/disable/reorder endpoints under `/admin/diagnostic-rules`.
- Produces: `POST /admin/diagnostic-rules/preview` returning the event produced by a supplied example.

- [ ] Write failing permission, validation, preview, reorder, disable, and audit tests.
- [ ] Run the pytest file and confirm routes return 404.
- [ ] Implement admin/royal CRUD, compile regex during validation, audit mutations without payload examples containing secrets, and disable instead of delete.
- [ ] Run the test file plus audit tests and commit with `git commit -m "feat(api): expose diagnostic rule administration"`.

### Task 4: Visual rule editor

**Files:**
- Modify: `apps/web/src/api.ts`
- Create: `apps/web/src/domains/diagnostics/DiagnosticRuleEditor.tsx`
- Create: `apps/web/src/domains/diagnostics/diagnostics.css`
- Test: `apps/web/src/domains/diagnostics/DiagnosticRuleEditor.test.tsx`
- Modify: `apps/web/src/pages/AdminEmergencyConfig.tsx`

**Interfaces:**
- Produces: image picker, click/tap coordinate placement, rule form, example preview, reorder, and disable controls.

- [ ] Add failing tests that an image click writes normalized coordinates, invalid examples show preview failure, and saved markers restore at the same position.
- [ ] Run the focused test and confirm the editor is absent.
- [ ] Implement typed API methods and editor using `ROBOT_PHOTOS`; calculate coordinates from `getBoundingClientRect()` and clamp to `[0, 1]`.
- [ ] Add the editor as a distinct «Ошибки и индикация» section, leaving Emergency field configuration intact.
- [ ] Run focused tests and commit with `git commit -m "feat(web): add visual diagnostic rule editor"`.

### Task 5: Automatic view and interactive markers

**Files:**
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/domains/robots/RobotDiagnosticDiagram.tsx`
- Modify: `apps/web/src/domains/robots/RobotCheckWorkspace.tsx`
- Create: `apps/web/src/domains/robots/diagnosticPresentation.ts`
- Test: `apps/web/src/domains/robots/RobotCheckWorkspace.test.tsx`
- Test: `apps/web/src/domains/robots/robotPhotoHotspots.test.ts`

**Interfaces:**
- Produces: `chooseAutomaticView(events)` selecting severity then rule order.
- Produces: markers that select an event and reveal its explanation; manual view remains stable until robot change or «Показать ошибку».

- [ ] Add failing tests for critical-over-warning selection, stable ties, unknown fallback, manual override, and marker-to-description linkage.
- [ ] Run focused tests and confirm the current wheel-only diagram fails them.
- [ ] Render server-provided markers alongside legacy wheel markers; use system severity colors and shape classes for point/outline/zone.
- [ ] Implement automatic selection only at initial robot/event load and explicit reset.
- [ ] Run focused tests and commit with `git commit -m "feat(web): visualize diagnostic events on robot photos"`.

### Task 6: End-to-end and full verification

**Files:**
- Create: `apps/web/e2e/operational/diagnostic-rules.spec.ts`
- Modify: `apps/web/e2e/operational/responsive-visual.spec.ts`

- [ ] Add a browser journey that creates a rule, previews it, opens the seeded robot, observes automatic front view, taps the marker, and sees the explanation.
- [ ] Run `./scripts/verify.sh api` and `./scripts/verify.sh web`.
- [ ] Run `cd apps/web && npm run test:e2e:linux`.
- [ ] Inspect changed robot-card and admin-editor baselines at 320, 390, 768, 1024, and 1440 px in both themes.
- [ ] Commit tests and intentional baselines with `git commit -m "test: cover diagnostic rule workflow"`.
