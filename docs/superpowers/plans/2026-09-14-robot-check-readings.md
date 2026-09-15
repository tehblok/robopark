# Robot Check Readings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Сделать проверку робота понятной: фиксированная сводка, нормализованные ошибки, единая настраиваемая разметка показаний и ограниченные кэши для 200 пользователей.

**Architecture:** Существующий Emergency payload остаётся внутри short-lived single-flight cache. API нормализует его в summary, diagnostic events и typed readings из глобального каталога; web отображает один робот и один выбранный диагностический блок. Существующие секции, правила ошибок и polling расширяются без второго параллельного механизма.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Pydantic, pytest, React, TypeScript, Vitest, Testing Library, CSS.

**Spec:** `docs/superpowers/specs/2026-09-14-robot-check-readings-design.md`

## Global Constraints

- Разметка едина для всех парков, моделей и роботов.
- Основная сводка фиксирована: АКБ 1, АКБ 2, скорость, диск, связь и свежесть.
- Уровень и `[+…s]` не участвуют в сопоставлении ошибки; уровень остаётся live-состоянием.
- Изменять разметку может только роль `admin`; остальные допущенные роли только читают.
- Не публиковать cookie, полный payload, `hud`, `sdcOptions`, исключения и host-пути.
- Сохранять совместимость старых секций, exact/regex-правил и URL.
- Использовать существующие дизайн-токены, responsive primitives и visibility polling.
- Делать изменения через TDD и не добавлять новый UI framework или cache dependency.

---

### Task 1: Normalize Summary and Notifications

**Files:**
- Modify: `apps/api/src/robopark_api/services/diagnostic_rules.py`
- Modify: `apps/api/src/robopark_api/services/emergency_snapshot.py`
- Modify: `apps/api/src/robopark_api/schemas.py`
- Test: `apps/api/tests/fixtures/emergency_robot.json`
- Test: `apps/api/tests/test_diagnostic_rules.py`
- Test: `apps/api/tests/test_emergency_snapshot.py`

**Interfaces:**
- Produces: `canonical_notification_body(value: object) -> str | None` and dual raw/canonical exact matching.
- Produces: snapshot fields `battery1_connected`, `battery2_connected`; `observed_at` derives from payload `timestamp` when valid.
- Preserves: `match_diagnostic_events_for_rules(...)` and current `EmergencySnapshotOut` fields.

- [ ] **Step 1: Add failing notification and summary tests**

```python
def test_notification_prefix_and_elapsed_time_do_not_define_rule():
    body = "/RoverChassis/Wheels/right_rear_wheel/MotorCalibration: CalibrationFault"
    assert canonical_notification_body(f"CRIT: [+8353.6s] {body}") == body

def test_structured_notification_uses_name_and_message():
    raw = {"level": 2, "name": "/RemoteControl/FleetApi/LastSuccessfulRequestAge:", "message": "Error", "timestamp": 3198.1}
    assert canonical_notification_body(raw) == "/RemoteControl/FleetApi/LastSuccessfulRequestAge: Error"
```

Add fixture assertions for battery percentages/connection flags, zero speed, `diskUsage=40`, `isOnline=true`, and payload timestamp conversion.

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```bash
cd apps/api && uv run --frozen --extra dev python -m pytest -p no:cacheprovider -q tests/test_diagnostic_rules.py tests/test_emergency_snapshot.py
```

Expected: failures for missing canonicalizer, connection fields, or payload-derived time.

- [ ] **Step 3: Implement canonical notification projection**

Parse structured `name/message/level/timestamp` first. For strings, remove only an optional leading `(CRIT|ERROR|WARN):` and optional `[+number s]`, then trim and collapse whitespace. Generate comparison candidates `{raw_text, canonical_body}`; exact rules compare normalized rule text against both, regex retains current raw behavior.

```python
_PREFIX = re.compile(r"^(?:(CRIT|ERROR|WARN):\s*)?(?:\[\+\d+(?:\.\d+)?s\]\s*)?")

def canonical_notification_body(value: Any) -> str | None:
    if isinstance(value, dict):
        name, message = str(value.get("name") or "").strip().rstrip(":"), str(value.get("message") or "").strip()
        text = f"{name}: {message}" if name and message else name or message
    elif isinstance(value, str):
        text = _PREFIX.sub("", value.strip(), count=1)
    else:
        return None
    return " ".join(text.split()) or None
```

Preserve actual severity on `DiagnosticEvent`; do not use persisted rule severity when the structured/legacy source supplies one.

- [ ] **Step 4: Extend the summary safely**

Return `battery1_connected`/`battery2_connected`; distinguish false from missing. Convert millisecond `timestamp` to an aware UTC datetime with range validation, otherwise let the router use receipt time. Keep `diskUsage` before legacy `disk`, and never coerce missing values to zero.

- [ ] **Step 5: Run focused tests and commit**

```bash
cd apps/api && uv run --frozen --extra dev python -m pytest -p no:cacheprovider -q tests/test_diagnostic_rules.py tests/test_emergency_snapshot.py tests/test_emergency_router.py
git add apps/api/src/robopark_api/services/diagnostic_rules.py apps/api/src/robopark_api/services/emergency_snapshot.py apps/api/src/robopark_api/schemas.py apps/api/tests/fixtures/emergency_robot.json apps/api/tests/test_diagnostic_rules.py apps/api/tests/test_emergency_snapshot.py
git commit -m "feat(api): normalize robot notifications and summary"
```

### Task 2: Add the Global Readings Catalog and API

**Files:**
- Create: `apps/api/alembic/versions/0027_emergency_readings.py`
- Modify: `apps/api/src/robopark_api/models.py`
- Modify: `apps/api/src/robopark_api/schemas.py`
- Create: `apps/api/src/robopark_api/services/emergency_readings.py`
- Create: `apps/api/src/robopark_api/routers/admin_emergency_readings.py`
- Modify: `apps/api/src/robopark_api/main.py`
- Modify: `apps/api/src/robopark_api/services/audit.py`
- Test: `apps/api/tests/test_models_migration.py`
- Create: `apps/api/tests/test_admin_emergency_readings.py`
- Create: `apps/api/tests/test_emergency_readings.py`

**Interfaces:**
- Produces: `EmergencyReading`, CRUD under `/admin/emergency-readings`, and `render_readings(db, payload, role) -> list[EmergencyReadingValue]`.
- Produces: `GET /admin/emergency-readings/discovered?vin=...` returning scalar `{path, value_type, example}` records with allow-listed, truncated examples.
- Consumes: existing `EmergencySection` IDs for diagnostic block grouping.

- [ ] **Step 1: Write failing migration/model tests**

Assert upgrade creates `emergency_readings` with a unique `(section_id, path)` pair and constraints for:

```text
display_kind: text|number|percent|distance|current|state
view: top|front|rear|left|right|isometric
label_direction: auto|left|right|top|bottom
x,y: finite 0..1
precision: 0..4
```

Columns: `id`, `section_id`, `path`, `label`, `display_kind`, `unit`, `precision`, `enabled_path`, `no_data_json`, `warning_below`, `warning_above`, `critical_below`, `critical_above`, `view`, `x`, `y`, `label_direction`, `is_enabled`, `sort_order`.

- [ ] **Step 2: Run migration tests and confirm failure**

```bash
cd apps/api && uv run --frozen --extra dev python -m pytest -p no:cacheprovider -q tests/test_models_migration.py
```

- [ ] **Step 3: Add typed schemas and pure renderer tests**

```python
class EmergencyReadingValue(BaseModel):
    id: int
    section_id: str
    label: str
    display: str
    state: Literal["normal", "warning", "critical", "unavailable"]
    view: DiagnosticView
    x: float
    y: float
    label_direction: Literal["auto", "left", "right", "top", "bottom"]
```

Test safe dotted lookup, formatting, zero preservation, optional `enabled_path`, configured no-data sentinels, thresholds, role-filtered disabled sections, and isolation when one reading is invalid.

- [ ] **Step 4: Implement renderer and admin CRUD**

Use Pydantic `extra="forbid"`, bounded strings/lists, canonical JSON for `no_data_json`, ETag for the catalog, and the same transactional conflict handling as diagnostic rules. Require approved `admin` exactly; royal GET/POST/PATCH/DELETE must return 403. Audit only changed field names, IDs, order, and enabled state.

The discovery endpoint normalizes VIN, uses the current Emergency cache, recursively lists scalar paths up to depth 12 and 1000 candidates, excludes `hud`, `sdcOptions`, cookies/tokens and strings over 128 characters, and truncates displayed examples to 80 characters.

- [ ] **Step 5: Run focused API tests and commit**

```bash
cd apps/api && uv run --frozen --extra dev python -m pytest -p no:cacheprovider -q tests/test_models_migration.py tests/test_admin_emergency_readings.py tests/test_emergency_readings.py
git add apps/api/alembic/versions/0027_emergency_readings.py apps/api/src/robopark_api/models.py apps/api/src/robopark_api/schemas.py apps/api/src/robopark_api/services/emergency_readings.py apps/api/src/robopark_api/routers/admin_emergency_readings.py apps/api/src/robopark_api/main.py apps/api/src/robopark_api/services/audit.py apps/api/tests/test_models_migration.py apps/api/tests/test_admin_emergency_readings.py apps/api/tests/test_emergency_readings.py
git commit -m "feat(api): add configurable robot readings"
```

### Task 3: Project Readings into Robot Snapshots

**Files:**
- Modify: `apps/api/src/robopark_api/services/emergency_snapshot.py`
- Modify: `apps/api/src/robopark_api/routers/emergency.py`
- Modify: `apps/api/src/robopark_api/schemas.py`
- Test: `apps/api/tests/test_emergency_snapshot.py`
- Test: `apps/api/tests/test_emergency_router.py`
- Test: `apps/api/tests/test_mechanic_emergency.py`
- Test: `apps/api/tests/test_driver_emergency_scope.py`

**Interfaces:**
- Consumes: `render_readings(db, payload, role)` from Task 2.
- Produces: `EmergencySnapshotOut.readings: list[EmergencyReadingValue]` and correct `observed_at`.

- [ ] **Step 1: Add failing response-contract and role tests**

Create one normal, one critical and one unavailable reading in two sections. Assert the snapshot includes only readings from sections visible to the caller, preserves configured placement, and keeps diagnostics/summary when one reading path is invalid.

- [ ] **Step 2: Run focused tests and confirm failure**

```bash
cd apps/api && uv run --frozen --extra dev python -m pytest -p no:cacheprovider -q tests/test_emergency_snapshot.py tests/test_emergency_router.py tests/test_mechanic_emergency.py tests/test_driver_emergency_scope.py
```

- [ ] **Step 3: Add readings to the snapshot projection**

Change the parser boundary to accept the role:

```python
def parse_emergency_snapshot(payload: dict[str, Any], *, vin: str, db: Session | None = None, role: str | None = None) -> dict[str, Any]:
    readings = render_readings(db, payload, role) if db is not None and role is not None else []
    return {**summary, "diagnostic_events": events, "readings": readings}
```

Pass `user.role` from the router. Use the normalized payload timestamp for `observed_at`; receipt time is fallback only.

- [ ] **Step 4: Run tests and commit**

```bash
cd apps/api && uv run --frozen --extra dev python -m pytest -p no:cacheprovider -q tests/test_emergency_snapshot.py tests/test_emergency_router.py tests/test_mechanic_emergency.py tests/test_driver_emergency_scope.py
git add apps/api/src/robopark_api/services/emergency_snapshot.py apps/api/src/robopark_api/routers/emergency.py apps/api/src/robopark_api/schemas.py apps/api/tests/test_emergency_snapshot.py apps/api/tests/test_emergency_router.py apps/api/tests/test_mechanic_emergency.py apps/api/tests/test_driver_emergency_scope.py
git commit -m "feat(api): expose configured robot readings"
```

### Task 4: Bound Caches and Clean Shared Files

**Files:**
- Modify: `apps/api/src/robopark_api/services/response_cache.py`
- Modify: `apps/api/src/robopark_api/services/emergency_cache.py`
- Modify: `apps/api/src/robopark_api/services/live_merge.py`
- Create: `apps/api/src/robopark_api/services/cache_cleanup.py`
- Modify: `apps/api/src/robopark_api/main.py`
- Modify: `apps/api/src/robopark_api/config.py`
- Modify: `apps/api/src/robopark_api/services/diagnostic_unknowns.py`
- Test: `apps/api/tests/test_response_cache.py`
- Test: `apps/api/tests/test_emergency_cache.py`
- Test: `apps/api/tests/test_live_merge.py`
- Test: `apps/api/tests/test_diagnostic_unknowns.py`
- Create: `apps/api/tests/test_cache_cleanup.py`

**Interfaces:**
- Produces: bounded `ResponseCache(..., max_entries=...)`, 512-entry Emergency LRU, 60-second stale ceiling, `LiveMergeStore.prune(...)`, and one lease-owned cleanup loop.
- Preserves: one upstream load per key/TTL and DB-session release before waits.

- [ ] **Step 1: Write failing eviction, concurrency and cleanup tests**

Test LRU eviction after `max_entries`, expired entry removal without access, `_flights` cleanup after success/error/cancellation, 200 same-key callers invoking the loader once, concurrency cap, stale result rejection after 60 seconds, and pruning old `.json/.error/.inflight/.lock/.tmp` without touching live claims.

- [ ] **Step 2: Run focused tests and confirm failure**

```bash
cd apps/api && uv run --frozen --extra dev python -m pytest -p no:cacheprovider -q tests/test_response_cache.py tests/test_emergency_cache.py tests/test_live_merge.py tests/test_cache_cleanup.py tests/test_diagnostic_unknowns.py
```

- [ ] **Step 3: Implement bounded stores and cleanup**

Use `OrderedDict` under the existing locks; move hits to the end and evict oldest entries after insert. Add a `BoundedSemaphore(settings.emergency_max_concurrency)` around only external HTTP. Keep TTL at 3 seconds and stale fallback at most 60 seconds. `LiveMergeStore.prune(now, ...)` must stat first, skip current live inflight PIDs, and tolerate races/individual filesystem errors.

The lease-owned hourly loop prunes cache files and only `DiagnosticUnknown.status == "new"`: delete unseen 30 days, then oldest until at most 1000 remain. Never delete `mapped` or `ignored` identities.

- [ ] **Step 4: Add deterministic 200-caller tests**

Use barriers/events rather than sleeps. Assert 200 callers return the same object, exactly one loader ran for one key, and 20 distinct keys never exceed the configured loader concurrency.

- [ ] **Step 5: Run focused tests and commit**

```bash
cd apps/api && uv run --frozen --extra dev python -m pytest -p no:cacheprovider -q tests/test_response_cache.py tests/test_emergency_cache.py tests/test_live_merge.py tests/test_live_merge_multiprocess.py tests/test_cache_cleanup.py tests/test_diagnostic_unknowns.py
git add apps/api/src/robopark_api/services/response_cache.py apps/api/src/robopark_api/services/emergency_cache.py apps/api/src/robopark_api/services/live_merge.py apps/api/src/robopark_api/services/cache_cleanup.py apps/api/src/robopark_api/main.py apps/api/src/robopark_api/config.py apps/api/src/robopark_api/services/diagnostic_unknowns.py apps/api/tests/test_response_cache.py apps/api/tests/test_emergency_cache.py apps/api/tests/test_live_merge.py apps/api/tests/test_cache_cleanup.py apps/api/tests/test_diagnostic_unknowns.py
git commit -m "perf(api): bound caches and prune stale state"
```

### Task 5: Build the Admin Readings Editor

**Files:**
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/pages/AdminEmergencyConfig.tsx`
- Create: `apps/web/src/domains/diagnostics/ReadingCatalogEditor.tsx`
- Modify: `apps/web/src/domains/diagnostics/diagnostics.css`
- Test: `apps/web/src/pages/AdminEmergencyConfig.test.tsx`
- Create: `apps/web/src/domains/diagnostics/ReadingCatalogEditor.test.tsx`
- Modify: `apps/web/src/domains/diagnostics/DiagnosticRuleEditor.test.tsx`

**Interfaces:**
- Consumes: Task 2 reading CRUD, discovery records and ETag.
- Produces: admin-only tabs `Ошибки` and `Показания`; one-record-at-a-time readings workflow.

- [ ] **Step 1: Add failing API/type and role tests**

Define `EmergencyReading`, `EmergencyReadingDraft`, `EmergencyDiscoveredField`, `ReadingState`, and CRUD/discovery methods in `api.ts`. Assert royal does not see editing controls and server 403 is mapped to access denied.

- [ ] **Step 2: Add failing editor interaction tests**

Test: enter sample robot, load discovered scalar fields, search, select `parktronics.lt`, receive auto-suggested `ltEnabled` and no-data sentinel, choose format/block, set point on a real robot view, set direction, preview, save, edit one item, reorder, disable and delete.

- [ ] **Step 3: Run focused web tests and confirm failure**

```bash
cd apps/web && npm test -- src/pages/AdminEmergencyConfig.test.tsx src/domains/diagnostics/ReadingCatalogEditor.test.tsx src/domains/diagnostics/DiagnosticRuleEditor.test.tsx
```

- [ ] **Step 4: Implement the compact editor**

Reuse `ROBOT_PHOTOS`, placement behavior and existing form primitives. Keep JSON path secondary/read-only after discovery. Use select labels `Число`, `Процент`, `Расстояние`, `Ток`, `Состояние`; hide thresholds until requested. Show one preview and one primary action. Preserve exact/regex compatibility in the Errors tab while wording the normal exact flow around the cleaned body.

- [ ] **Step 5: Run focused tests and commit**

```bash
cd apps/web && npm test -- src/pages/AdminEmergencyConfig.test.tsx src/domains/diagnostics/ReadingCatalogEditor.test.tsx src/domains/diagnostics/DiagnosticRuleEditor.test.tsx src/domains/diagnostics/diagnosticApi.test.ts
git add apps/web/src/api.ts apps/web/src/pages/AdminEmergencyConfig.tsx apps/web/src/domains/diagnostics/ReadingCatalogEditor.tsx apps/web/src/domains/diagnostics/diagnostics.css apps/web/src/pages/AdminEmergencyConfig.test.tsx apps/web/src/domains/diagnostics/ReadingCatalogEditor.test.tsx apps/web/src/domains/diagnostics/DiagnosticRuleEditor.test.tsx
git commit -m "feat(web): add robot readings editor"
```

### Task 6: Redesign the Robot Check Workspace

**Files:**
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/domains/robots/RobotCheckSummary.tsx`
- Modify: `apps/web/src/domains/robots/RobotCheckWorkspace.tsx`
- Modify: `apps/web/src/domains/robots/RobotDiagnosticDiagram.tsx`
- Modify: `apps/web/src/domains/robots/diagnosticPresentation.ts`
- Create: `apps/web/src/domains/robots/readingPlacement.ts`
- Modify: `apps/web/src/domains/robots/robot-check.css`
- Test: `apps/web/src/domains/robots/RobotCheckWorkspace.test.tsx`
- Test: `apps/web/src/domains/robots/diagnosticPresentation.test.ts`
- Create: `apps/web/src/domains/robots/readingPlacement.test.ts`

**Interfaces:**
- Consumes: `EmergencySnapshot.readings` from Task 3.
- Produces: compact five-value summary, one selected diagnostic block, mixed error/reading overlay and collision-free label placements.

- [ ] **Step 1: Add failing summary and block-selection tests**

Assert separate АКБ states, zero speed, disk, connection/freshness, automatic critical block/view selection, manual override, one expanded block on desktop/mobile, and partial rendering when readings fail.

- [ ] **Step 2: Add failing pure placement tests**

Define:

```ts
export type LabelBox = { id: string; x: number; y: number; width: number; height: number; direction: 'auto'|'left'|'right'|'top'|'bottom' }
export type PlacedLabel = LabelBox & { left: number; top: number; collapsed: boolean }
export function placeLabels(items: LabelBox[], frame: { width: number; height: number }, robotBox: DOMRectReadOnly): PlacedLabel[]
```

Test priority order `error > critical reading > selected > warning`, manual direction, no overlap with robot/placed labels, stable output, and side-list fallback when no candidate fits.

- [ ] **Step 3: Run focused tests and confirm failure**

```bash
cd apps/web && npm test -- src/domains/robots/RobotCheckWorkspace.test.tsx src/domains/robots/diagnosticPresentation.test.ts src/domains/robots/readingPlacement.test.ts
```

- [ ] **Step 4: Implement summary, overlay and responsive layout**

Use measured frame/image boxes with `ResizeObserver`; placement math remains pure. Render markers as 44 px buttons with smaller visible dots, leader lines via positioned elements/SVG, and labels only for errors, critical/warning readings or selected points. If placement returns `collapsed`, render numbered entries in one side list. Apply existing semantic theme variables; do not hardcode light-only colors.

- [ ] **Step 5: Preserve polling behavior and add recovery assertions**

Reuse `useVisibilityPolling.ts` and existing exponential schedule instead of adding timers. Extend its tests only for immediate manual refresh/visibility resume and ensure one in-flight request remains the invariant.

- [ ] **Step 6: Run focused tests and commit**

```bash
cd apps/web && npm test -- src/domains/robots/RobotCheckWorkspace.test.tsx src/domains/robots/diagnosticPresentation.test.ts src/domains/robots/readingPlacement.test.ts src/domains/robots/useVisibilityPolling.test.tsx src/domains/robots/polling.test.ts
git add apps/web/src/api.ts apps/web/src/domains/robots/RobotCheckSummary.tsx apps/web/src/domains/robots/RobotCheckWorkspace.tsx apps/web/src/domains/robots/RobotDiagnosticDiagram.tsx apps/web/src/domains/robots/diagnosticPresentation.ts apps/web/src/domains/robots/readingPlacement.ts apps/web/src/domains/robots/robot-check.css apps/web/src/domains/robots/RobotCheckWorkspace.test.tsx apps/web/src/domains/robots/diagnosticPresentation.test.ts apps/web/src/domains/robots/readingPlacement.test.ts apps/web/src/domains/robots/useVisibilityPolling.test.tsx
git commit -m "feat(web): redesign robot check readings"
```

### Task 7: Integration, Accessibility and Load Verification

**Files:**
- Modify: `apps/web/e2e/operational/responsive-visual.spec.ts`
- Modify: `apps/web/e2e/operational/robots.spec.ts`
- Create: `apps/api/tests/test_emergency_load.py`
- Modify: relevant snapshots/fixtures only when an intentional visual change requires it

**Interfaces:**
- Consumes: all previous tasks.
- Produces: verified end-to-end feature and objective cache/load evidence.

- [ ] **Step 1: Add browser scenarios**

Cover admin configuration and mechanic read-only use at 320, 390, 768, 1024 and 1440 px in light/dark themes. Assert no horizontal overflow, labels do not intersect the measured robot rectangle, keyboard selection works, touch-targets are at least 44 px, and 200% zoom keeps actions reachable.

- [ ] **Step 2: Add deterministic load/soak harness**

`test_emergency_load.py` must exercise 200 callers for one VIN and 200 callers across 20 VINs with a fake upstream, assert call de-duplication/concurrency, then perform repeated bounded-key cycles and assert cache/file counts plateau. Keep the one-hour RSS soak as an explicit release command, not a default unit test.

- [ ] **Step 3: Run focused integration tests**

```bash
cd apps/api && uv run --frozen --extra dev python -m pytest -p no:cacheprovider -q tests/test_emergency_load.py tests/test_emergency_router.py tests/test_admin_emergency_readings.py
cd apps/web && npm test -- src/domains/robots src/domains/diagnostics src/pages/AdminEmergencyConfig.test.tsx
```

- [ ] **Step 4: Run full verification**

```bash
./scripts/verify.sh api
./scripts/verify.sh web
```

Expected: API lint/format/tests pass; web lint/build/tests/navigation checks pass.

- [ ] **Step 5: Review the final diff and commit verification adjustments**

```bash
git diff --check
git status --short
git add apps/api/tests/test_emergency_load.py apps/web/e2e/operational/responsive-visual.spec.ts apps/web/e2e/operational/robots.spec.ts
git commit -m "test: verify robot check under load"
```
