# Task tabs and related repairs

Work keeps the main task separate from Open tasks, Closed tasks and Robot check.
The Task panel contains the description, comments, attachments and allowed
Tracker actions. It stays mounted while switching tabs so an unsent comment or
selected attachment is not discarded. Opening another task starts a separate
form. Repairs and diagnostics load only when their tab is opened.

Related lists request the same exact robot and allowed park/queue, type `repair`,
with any priority. The current issue is excluded. Both lists use oldest-first
ordering and pages of ten. Closed repairs are limited to the last 14 days by
Tracker's `resolvedAt`, regardless of creation or update date. The API narrows
the upstream search by resolution date and applies the exact rolling cutoff
before totals and pagination, also on cache hits. Missing or invalid resolution
dates are excluded. Other Tracker history lists retain their existing scope.
The API requires a valid `robot_exact` for
`related_repairs=true`; the type and robot filters run before pagination. The
ordinary Work list remains an open-blocker queue. Existing permissions govern
viewing and changing additional repairs.

`blocker` in the URL retains the original issue key through any number of
additional tasks. `view` selects the task tab; `check_tab` selects the embedded
robot-check section. Returning to the main blocker resets those view parameters
while preserving park, queue, status and list pagination. Closing an additional
repair also returns to the original blocker. Changing list filters preserves the
root issue. Root keys are validated and encoded as path segments, never accepted
as arbitrary return URLs.

The embedded check reuses RobotCheckWorkspace, including live data, diagnostics,
map and its existing access checks. The return link remains in the task header,
so checking the robot does not leave the work context.

## Verification

- API: 1074 tests passed, including repair type/priority, exact identity, scope,
  pagination and permission checks for reading/commenting non-blocker repairs.
- Web: 1504 unit tests passed; production build, lint (existing warnings),
  navigation and theme contrast checks passed.
- Dedicated Chromium cases cover desktop and phone, deferred related/check
  loading, comment draft preservation, writing to the selected repair, two nested
  repairs, check navigation/reload/root return and closed-repair pagination.
- Full Chromium regression: 198 passed. After the final embedded-check width
  adjustment, all four dedicated tab/navigation/layout cases passed again.
- Live verification on robot 1217: related history included a medium-priority
  repair; opening it retained blocker SDCFLEETOPS-370188. Its check loaded actual
  robot data and the root link returned to the original blocker and Task tab.

### Closed repair history window — 2026-09-06

- Full API suite: 1075 passed; date boundaries, timezone offsets, invalid/missing
  dates, cached rows aging out and pagination are covered.
- Full web suite: 1504 passed; full Chromium suite: 198 passed. Build, lint
  (existing warnings), navigation and contrast checks passed.
- Live read-only check of blocker SDCFLEETOPS-370188 / robot 1217 shows five
  closed repairs in the 14-day window, with the period stated in the tab.
