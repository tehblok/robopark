# Task 6 report — task-first work and robot UX

## Outcome

- Interface A now uses the approved task-first composition: the selected task is a flat central Repair/Check/Chat workflow, robot/operator context is a separate right rail, and the workflow transition is a bottom action bar.
- At 320/390/412/899 px the context becomes a closed disclosure and the primary action remains sticky above the mobile navigation. At 1440 px the context is a visible independent rail.
- `TaskController` and `RobotCheckController` remain the only live owners. Classic/A switches retain the same comment draft, selected `File`, review form, polling cache, diagram portal and camera lifecycle; no second snapshot request is created.
- Classic no longer receives `a-task-layout` or `a-robot-layout`. Its content and controls remain in the stable shared subtree.
- Robot A retains the six existing robot photographs, automatic diagnostic angle, configured readings/blocks, two batteries, speed, disk, LTE/connection/two SIM values, errors, map and scanner fallbacks.
- Tracker control wrappers (`<[...]>`, `<{...}>`) are removed from display text without discarding useful inline text. Panel toggles show human text while retaining the full accessible label. Recent robots no longer repeat a VIN when the original query already is that VIN.
- Existing task workflow behavior remains unchanged: local mechanic claim/takeover, `ROBOT_SUSPENSION` outbox delivery, five Moscow working hours (09:00–21:00), parts write-off, chat/photo/handoff, one-photo review close, operator approval, external close reconciliation, bot-authored idempotent outbox and pending sync.

## TDD evidence

RED runs captured the missing A layout module, unstripped Tracker wrappers, technical collapse copy, A robot class leaking into Classic, and repeated recent VIN. The first browser owner-preservation run also failed because the A wrapper changed element type across mode switches; it lost the selected file and review form state. The wrapper was made structurally stable before proceeding.

Focused GREEN:

```text
Task/layout/markup/collapse: 17 passed
Task + robot controllers/layout: 132 passed
Recent robots: 7 passed
Interface owner preservation: 5 passed
```

## Verification

- Full web unit suite: `npm test` — **151 files, 2095 tests passed** after review round 1.
- Relevant API workflow/Tracker/Emergency suite: `.venv/bin/python -m pytest -p no:cacheprovider -q tests/test_task_lifecycle.py tests/test_task_timeline.py tests/test_tracker_read.py tests/test_tracker_outbox.py tests/test_emergency_snapshot.py tests/test_emergency_router.py tests/test_emergency_readings.py tests/test_emergency_sections.py` — **165 passed**, one pre-existing Starlette/httpx deprecation warning.
- Relevant Chromium E2E: `npx playwright test e2e/operational/task-robot-composition.spec.ts e2e/operational/interface-work.spec.ts e2e/operational/task-lifecycle.spec.ts e2e/operational/robots.spec.ts e2e/operational/compact-ui.spec.ts --project=chromium` — **65 passed** after review round 1.
- Visual matrix generated for work and robot at **320/390/412/899/1440**, light/dark. The 1440 task and robot screenshots were manually compared with the approved reference; zone order, rail separation, action hierarchy and real robot visual were checked.
- `npm run build` — exit 0.
- `npm run check-nav` — 30 route ids, OK.
- `npm run check:contrast` — all printed light/dark pairs above AA threshold.
- `npm run lint` — exit 0 with the repository's existing warnings; no new error.
- `git diff --check` — clean.

The physical OnePlus camera cannot be exercised from this workspace. The browser contract is covered through the existing real `MediaStream` lifecycle boundary with fake tracks: HTTPS/insecure-context copy, permission denial, missing camera, native `BarcodeDetector`, lazy QR fallback, manual input, cancellation and track cleanup all remain green.

## Files

- `apps/web/src/domains/work/IssueWorkbench.tsx`
- `apps/web/src/domains/work/TaskFirstTaskLayout.tsx`
- `apps/web/src/domains/work/TaskFirstTaskLayout.test.tsx`
- `apps/web/src/domains/robots/RobotCheckWorkspace.tsx`
- `apps/web/src/domains/robots/TaskFirstRobotLayout.tsx`
- `apps/web/src/domains/robots/TaskFirstRobotLayout.test.tsx`
- `apps/web/src/domains/robots/RobotsPage.tsx`
- `apps/web/src/domains/robots/RobotsPage.test.ts`
- `apps/web/src/components/tracker/issueDescription.ts`
- `apps/web/src/components/tracker/issueDescription.test.ts`
- `apps/web/src/design-system/layout/PageLayout.tsx`
- `apps/web/src/design-system/layout/PageLayout.test.tsx`
- `apps/web/src/app/interface/interface-a.css`
- `apps/web/e2e/operational/task-robot-composition.spec.ts`

The pre-existing uncommitted edit in `progress.md` was not modified or staged by Task 6.

## Review round 1

- The A footer lifecycle controls are now projected from the same `IssueActionsPanel` action runner as Classic. Its ref lock is synchronous, all repeated controls are disabled/busy, `ApiError` is caught into the existing visible alert, and recovery reuses the same idempotent lifecycle mutation path. Browser coverage proves a 503 double-click produces exactly one POST, no `pageerror`, and a later retry succeeds.
- The A context is no longer placeholder copy. The single existing `RobotCheckController` projects its current snapshot into the task controller: error state, LTE label and connection type, both SIM signals, both batteries, and observation time. No second snapshot owner or cache was added. Operator presence is not claimed without presence data.
- The repair column now exposes the approved, explicit sequence `Проверить робота → Запчасти → Что было сделано`; its controls open the existing robot check, parts panel, and shared comment form instead of creating duplicate forms or actions.
- On phone widths the DOM and visual order is task header, closed context disclosure, repair workflow, sticky action. Desktop retains the right context rail. The 320/390/412/899/1440 light/dark matrix asserts this order, real values, geometry, accessibility sizing, and the Classic boundary.
- Tracker wrappers now remove only `<[`, `]>`, `<{`, `}>` control delimiters, retaining meaningful inner text on standalone and inline forms.

Review RED/GREEN evidence:

```text
Wrapper standalone inner text: RED 1 failed → GREEN
Mobile task-zone order: RED 1 failed → GREEN
Shared projected action runner: RED missing footer control → GREEN 28 tests
A 503 double-click/recovery browser contract: GREEN, exactly 1 first POST and 0 page errors
Visual matrix first run: RED 10/10 (13 px context label) → GREEN 11/11 after 14 px accessibility fix
Full web: 151 files, 2095 tests passed
```
