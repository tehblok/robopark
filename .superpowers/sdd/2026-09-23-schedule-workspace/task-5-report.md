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

## Follow-up browser correction

The first out-of-sandbox browser run executed all four scenarios and exposed two fixture/test setup defects:

- the shared schedule readiness marker waited for mobile day cards, which are intentionally hidden in desktop team view; it now waits for the loaded, visible `.rp-page-layout.rp-schedule` root;
- the fixed schedule entry used 23 September while `installOperational` freezes the browser clock on 2 September; the fixture entry and explicit phone selection now use 2 September, keeping the meaningful `Моя смена` assertion inside the visible week.

The one permitted repeat then produced 1 PASS and 3 FAIL: the desktop sticky/range scenario passed, while royal edit and both phone scenarios exposed the date mismatch above. No additional browser run was made.

Independent review also found that the original sticky assertion checked only computed CSS. The scenario now supplies enough employee rows for vertical overflow, scrolls the team container horizontally and vertically, and asserts that header, corner and employee coordinates remain pinned within a 1 px tolerance. The correction keeps the range, royal-edit and phone content/geometry assertions intact.

The subsequent four-scenario verification passed desktop sticky/range and both phone widths. Royal edit reached and saved the update, but its final global text assertion matched both the visible desktop matrix and the intentionally hidden mobile cards. The final assertion is scoped to the visible `schedule-team-grid` so it verifies the updated desktop entry without violating Playwright strict locator semantics.

- `npx playwright test e2e/operational/schedule-workspace.spec.ts --project=chromium --workers=1 --grep "royal can edit" --output=/private/tmp/robopark-schedule-royal-edit`
  - PASS: 1 test.
