# Schedule workspace implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the schedule list with a dedicated responsive calendar for personal availability, team coverage and royal planning.

**Architecture:** Preserve the existing schedule entry model and permissions. Add a pure calendar projection layer, window API requests to the visible period, and render separate desktop matrix and mobile day-card presentations from the same projected data. Bulk patterns expand to existing bulk entries on the server; unrelated features from the attached example are excluded.

**Tech Stack:** FastAPI, SQLAlchemy, React 19, TypeScript, CSS Grid, Vitest.

**Spec:** `docs/superpowers/specs/2026-09-23-classic-workflows-inventory-schedule-design.md`

## Global Constraints

- Users edit only their own periods; admin reads assigned-park schedules; royal edits team schedules.
- Schedule kinds remain `shift`, `vacation`, `sick`.
- Time is stored with timezone and displayed in Europe/Moscow.
- Desktop matrix is never squeezed into phone layout; phone uses calendar/day cards.
- Requests are bounded to the visible period and park; no full-history fetch.
- No salary, payroll, game or unrelated example feature.

## Review Focus

- Month crossing DST or year boundary still produces correct Moscow day columns (Task 1 test).
- Two overlapping entries for one employee show a warning without dropping either entry (Task 2 test).
- Admin cannot mutate through UI or API even if controls are forged (Task 3 test).
- A 4/4 pattern beginning mid-month produces exactly the expected shift dates (Task 4 test).
- A 390 px viewport renders day cards with no horizontally compressed employee matrix (Task 5 test).

---

### Task 1: Add pure calendar projection helpers

**Files:**
- Create: `apps/web/src/domains/shift/scheduleCalendar.ts`
- Create: `apps/web/src/domains/shift/scheduleCalendar.test.ts`

**Interfaces:**
- `visibleRange(anchor: Date, view: 'week'|'month'): { start: Date; end: Date; days: Date[] }`.
- `projectSchedule(items, days): Map<number, Map<string, ScheduleEntry[]>>`.
- `formatDayKey(date): string` always uses Europe/Moscow.

- [ ] **Step 1: Write failing boundary tests**

```ts
it('projects a year-crossing Moscow week', () => {
  const range = visibleRange(new Date('2026-12-31T12:00:00+03:00'), 'week')
  expect(range.days.map(formatDayKey)).toEqual(['2026-12-28','2026-12-29','2026-12-30','2026-12-31','2027-01-01','2027-01-02','2027-01-03'])
})
```

- [ ] **Step 2: Run failing tests**

Run: `cd apps/web && npm test -- --run src/domains/shift/scheduleCalendar.test.ts`

- [ ] **Step 3: Implement pure helpers and rerun**

Use `Intl.DateTimeFormat` with `Europe/Moscow`; compare half-open entry ranges against day ranges so overnight shifts appear on both affected days.

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/domains/shift/scheduleCalendar.ts apps/web/src/domains/shift/scheduleCalendar.test.ts
git commit -m "feat(schedule): add calendar projection"
```

### Task 2: Window schedule loading and stable employee metadata

**Files:**
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/domains/shift/ScheduleWorkspace.tsx`
- Modify: `apps/web/src/domains/shift/ScheduleWorkspace.test.tsx`
- Test: `apps/api/tests/test_schedules.py`

**Interfaces:**
- `api.schedules({ parkId, ownerUserId, startAt, endAt })` serializes the existing range query.
- Workspace reloads only when park, visible range or access principal changes.
- Employee metadata is limited to active approved mechanics/operators in the selected park.

- [ ] **Step 1: Add failing API-call and stale-response tests**

```tsx
it('requests only the visible month and ignores the previous park response', async () => {
  renderSchedule({ parkId: 7, anchor: '2026-09-15' })
  expect(apiClient.schedules).toHaveBeenCalledWith(expect.objectContaining({ parkId: 7, startAt: expect.any(String), endAt: expect.any(String) }))
})
```

- [ ] **Step 2: Run failing tests**

Run: `cd apps/web && npm test -- --run src/domains/shift/ScheduleWorkspace.test.tsx`

- [ ] **Step 3: Implement request windowing and cancellation generation**

Keep the last successful window visible during refresh, reject late park/principal responses, and use the existing API start/end filters. Add an API test that a range over the server limit remains bounded and ordered.

- [ ] **Step 4: Run tests and commit**

Run: `cd apps/api && pytest -q tests/test_schedules.py -k list`

Run: `cd apps/web && npm test -- --run src/domains/shift/ScheduleWorkspace.test.tsx`

```bash
git add apps/web/src/api.ts apps/web/src/domains/shift/ScheduleWorkspace.tsx apps/web/src/domains/shift/ScheduleWorkspace.test.tsx apps/api/tests/test_schedules.py
git commit -m "perf(schedule): window calendar requests"
```

### Task 3: Build My calendar and Team views

**Files:**
- Create: `apps/web/src/domains/shift/ScheduleCalendar.tsx`
- Create: `apps/web/src/domains/shift/ScheduleCalendar.test.tsx`
- Create: `apps/web/src/domains/shift/ScheduleTeamGrid.tsx`
- Create: `apps/web/src/domains/shift/ScheduleTeamGrid.test.tsx`
- Modify: `apps/web/src/domains/shift/ScheduleWorkspace.tsx`
- Modify: `apps/web/src/domains/shift/ScheduleWorkspace.css`
- Modify: `apps/web/src/domains/shift/ScheduleWorkspace.test.tsx`

**Interfaces:**
- Internal Tabs: `Мой календарь`, `Команда`, `Планирование` according to role.
- `ScheduleCalendar` renders month/week controls and selected-day cards.
- `ScheduleTeamGrid` renders sticky employee/date headers on desktop only.

- [ ] **Step 1: Write failing role and rendering tests**

```tsx
it('shows personal calendar to mechanics and read-only team to admin', () => {
  const mechanicView = renderSchedule({ user: mechanic })
  expect(mechanicView.getByRole('tab', { name: 'Мой календарь' })).toBeVisible()
  mechanicView.unmount()
  renderSchedule({ user: admin })
  expect(screen.getByRole('tab', { name: 'Команда' })).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Добавить период' })).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Run failing tests**

Run: `cd apps/web && npm test -- --run src/domains/shift/ScheduleCalendar.test.tsx src/domains/shift/ScheduleTeamGrid.test.tsx src/domains/shift/ScheduleWorkspace.test.tsx`

- [ ] **Step 3: Implement calendar, team grid and responsive CSS**

The desktop grid uses `grid-template-columns: minmax(12rem,18rem) repeat(var(--day-count), minmax(7rem,1fr))`, sticky first column/header and its own overflow container. At max-width 719 px the team matrix is hidden in favour of selected-day cards grouped by employee.

- [ ] **Step 4: Run tests and commit**

Run the same command as Step 2 and expect PASS.

```bash
git add apps/web/src/domains/shift/ScheduleCalendar.tsx apps/web/src/domains/shift/ScheduleCalendar.test.tsx apps/web/src/domains/shift/ScheduleTeamGrid.tsx apps/web/src/domains/shift/ScheduleTeamGrid.test.tsx apps/web/src/domains/shift/ScheduleWorkspace.tsx apps/web/src/domains/shift/ScheduleWorkspace.css apps/web/src/domains/shift/ScheduleWorkspace.test.tsx
git commit -m "feat(schedule): add personal and team calendars"
```

### Task 4: Add royal planning patterns and period copy

**Files:**
- Modify: `apps/api/src/robopark_api/schedule_schemas.py`
- Modify: `apps/api/src/robopark_api/services/schedules.py`
- Modify: `apps/api/src/robopark_api/routers/schedules.py`
- Test: `apps/api/tests/test_schedules.py`
- Create: `apps/web/src/domains/shift/SchedulePlanner.tsx`
- Create: `apps/web/src/domains/shift/SchedulePlanner.test.tsx`
- Modify: `apps/web/src/domains/shift/ScheduleWorkspace.tsx`
- Modify: `apps/web/src/api.ts`

**Interfaces:**
- `SchedulePattern = 'none'|'5/2'|'2/2'|'4/4'`.
- `POST /schedules/pattern` accepts park, owners, kind, start/end times, date range and pattern.
- Existing `/schedules/copy` remains the period-copy endpoint.

- [ ] **Step 1: Write failing exact-date pattern tests**

```python
def test_four_on_four_off_pattern_dates(client):
    rows = create_pattern(client, pattern="4/4", start="2026-09-03", end="2026-09-14")
    assert [row["start_at"][:10] for row in rows] == ["2026-09-03", "2026-09-04", "2026-09-05", "2026-09-06", "2026-09-11", "2026-09-12", "2026-09-13", "2026-09-14"]
```

Test royal-only access, duplicate owner IDs, invalid range and a maximum 366-day window.

- [ ] **Step 2: Run failing API tests**

Run: `cd apps/api && pytest -q tests/test_schedules.py -k pattern`

- [ ] **Step 3: Implement bounded pattern expansion**

Expand calendar dates server-side to ordinary `ScheduleEntry` rows with one series ID. Reject more than 50 owners, 366 days or 5000 generated entries before inserting.

- [ ] **Step 4: Add and test the royal planner**

```tsx
it('submits selected employees and a 4/4 pattern', async () => {
  render(<SchedulePlanner employees={employees} apiClient={apiClient} parkId={7} />)
  await choosePatternAndSubmit('4/4')
  expect(apiClient.schedulePattern).toHaveBeenCalledWith(expect.objectContaining({ pattern: '4/4', owner_user_ids: [11, 12] }))
})
```

Run: `cd apps/web && npm test -- --run src/domains/shift/SchedulePlanner.test.tsx src/domains/shift/ScheduleWorkspace.test.tsx`

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/robopark_api/schedule_schemas.py apps/api/src/robopark_api/services/schedules.py apps/api/src/robopark_api/routers/schedules.py apps/api/tests/test_schedules.py apps/web/src/api.ts apps/web/src/domains/shift
git commit -m "feat(schedule): plan repeating shifts"
```

### Task 5: Verify schedule desktop/mobile behaviour

**Files:**
- Modify: `apps/web/e2e/operational/routeFixtures.ts`
- Modify: `apps/web/e2e/operational/interface-geometry.spec.ts`
- Create: `apps/web/e2e/operational/schedule-workspace.spec.ts`

**Interfaces:**
- Desktop verifies sticky team grid, range navigation and royal edit.
- Phone verifies personal selected-day cards and absence of compressed team grid.

- [ ] **Step 1: Add focused browser scenarios**

```ts
test('phone schedule uses day cards without document overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openRouteFixture(page, 'schedule', mechanicUser)
  await expect(page.getByTestId('schedule-day-cards')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true)
})
```

- [ ] **Step 2: Run only schedule and geometry specs**

Run: `cd apps/web && npx playwright test e2e/operational/schedule-workspace.spec.ts e2e/operational/interface-geometry.spec.ts --project=chromium --workers=1`

- [ ] **Step 3: Fix only confirmed schedule geometry defects and rerun**

Use schedule-local CSS for matrix sizing and shared design tokens for panels/actions. The second run must pass with the same command.

- [ ] **Step 4: Commit**

```bash
git add apps/web/e2e/operational/schedule-workspace.spec.ts apps/web/e2e/operational/interface-geometry.spec.ts apps/web/e2e/operational/routeFixtures.ts apps/web/src/domains/shift
git commit -m "test(schedule): verify responsive workspace"
```
