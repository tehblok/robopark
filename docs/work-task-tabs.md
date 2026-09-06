# Task tabs and related repairs

Work keeps the main task separate from Open tasks, Closed tasks and Robot check.
The Task panel contains the description, comments, attachments and allowed
Tracker actions. It stays mounted while switching tabs so an unsent comment or
selected attachment is not discarded. Opening another task starts a separate
form. Repairs and diagnostics load only when their tab is opened.

Related lists request the same exact robot and allowed park/queue, type `repair`,
with any priority. The current issue is excluded. Both lists use oldest-first
ordering and pages of ten. The API requires a valid `robot_exact` for
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
