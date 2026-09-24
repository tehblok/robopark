# Task 5 report: schedule workspace browser coverage

## Changed files

- `apps/web/e2e/operational/schedule-workspace.spec.ts`
  - verifies the desktop royal team matrix has sticky headers/employee column and an internal month overflow area;
  - verifies range navigation changes the visible dates;
  - verifies a royal user can edit and save another employee's schedule entry;
  - verifies 360 px and 390 px phone layouts use selected-day cards, hide the desktop team matrix and do not overflow the document.
- `apps/web/e2e/operational/routeFixtures.ts`
  - adds schedule participants to the operational fixture;
  - exports the schedule entry used by the edit scenario;
  - updates the schedule readiness marker to the current day-card workspace.
- `apps/web/e2e/operational/interface-geometry.spec.ts`
  - replaces the removed legacy schedule-list selector with the current day-card marker.

No production files were changed.

## Verification

- `npx oxlint e2e/operational/schedule-workspace.spec.ts e2e/operational/routeFixtures.ts e2e/operational/interface-geometry.spec.ts`
  - PASS (exit 0).
- `PLAYWRIGHT_OUTPUT_DIR=/private/tmp/robopark-schedule-e2e npx playwright test e2e/operational/schedule-workspace.spec.ts --project=chromium --workers=1 --list`
  - PASS: 4 tests discovered in 1 file.
- `npm test -- --run src/domains/shift/ScheduleCalendar.test.tsx src/domains/shift/ScheduleTeamGrid.test.tsx src/domains/shift/ScheduleWorkspace.test.tsx src/domains/shift/SchedulePlanner.test.tsx`
  - PASS: 4 files, 18 tests.
- `npx playwright test e2e/operational/schedule-workspace.spec.ts --project=chromium --workers=1`
  - BLOCKED before test execution by sandbox filesystem permissions: Playwright could not unlink or open `apps/web/test-results/.last-run.json` (`EPERM`). Per the task fallback, no second browser run and no full geometry matrix were started.
- `git diff --check`
  - PASS.
