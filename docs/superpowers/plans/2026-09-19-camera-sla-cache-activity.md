# Camera, SLA, Cache and Activity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore camera scanning and fast navigation, correct working-hour SLA and duration formatting, and show limited last-activity metadata in user management.

**Architecture:** Keep the existing React resource store and FastAPI models. Fix the site permission header at the source, calculate deadlines on the API, reuse cached protected data only in memory within an unchanged access scope, and store one latest-activity record per user. Resolve approximate IP location asynchronously so an external outage cannot slow sign-in.

**Tech Stack:** React 19, TypeScript, Vitest, Playwright, FastAPI, SQLAlchemy, Alembic, pytest, nginx.

**Spec:** `docs/superpowers/specs/2026-09-19-camera-sla-cache-activity-design.md`

## Global Constraints

- Working SLA: five hours, daily 09:00–21:00 `Europe/Moscow`, from the latest actual transition into «В очереди»; no invented creation-time start.
- Durations everywhere use one-decimal hours in presentation; timestamps, scheduling boundaries, API values, sorting, and thresholds retain full precision.
- Never persist authenticated page snapshots to localStorage or a service-worker API cache. Clear protected memory on logout, access change, 401, or 403.
- IP geolocation: server-side HTTPS `ipwho.is` only, two-second timeout, seven-day temporary cache, at most 800 lookups/day; failure never blocks the product. A public IP is disclosed to this provider as approved by the user.
- Only the existing user-management permission gate reveals IP, device, and approximate city/region/country. Do not claim these prove physical presence.
- Test desktop and phone layouts; honor `prefers-reduced-motion`.

---

### Task 1: Camera policy and scanner lifecycle

**Files:** Modify `apps/web/nginx.conf`, `apps/web/src/domains/robots/RobotScanner.tsx`, `apps/web/src/domains/robots/RobotScanner.test.tsx`; add or update a browser smoke test under `apps/web/e2e/` if the nginx preview harness supports response-header checks.

**Interfaces:** `RobotScanner` props remain unchanged; browser gets `Permissions-Policy: camera=(self), microphone=(), geolocation=()` on the app document.

- [ ] Add a failing test in `RobotScanner.test.tsx`: a policy-denied `getUserMedia` result must say that site/device policy blocked the camera, leave manual input available, and stop any opened tracks. Run `cd apps/web && npm test -- RobotScanner.test.tsx`; expect failure from the current generic/permission message.
- [ ] Add a failing nginx/browser check that requests `/index.html` and asserts the effective `Permissions-Policy` allows same-origin camera while disallowing microphone/geolocation. Run it against the current nginx image; expect `camera=()` failure.
- [ ] Update all nginx `add_header Permissions-Policy` instances consistently; fix scanner startup/retry only as required by the failing tests. Keep capture user-initiated and stop tracks on close/background/unmount.
- [ ] Rerun targeted tests, `nginx -t` in the web image, and a browser smoke with a fake camera. Commit the tested camera fix.

### Task 2: Working-hour SLA and common duration display

**Files:** Modify `apps/api/src/robopark_api/services/tracker_client.py`, `apps/api/tests/test_tracker_read.py`, `apps/web/src/domains/work/RepairSla.tsx`, `apps/web/src/domains/work/RepairSla.test.tsx`, `apps/web/src/components/tracker/issue-utils.ts`, `apps/web/src/components/tracker/issue-utils.test.ts`, `apps/web/src/domains/analytics/analyticsModel.ts`, `apps/web/src/domains/insights/OperationsPanels.tsx`, `apps/web/src/domains/shift/OverviewSections.tsx`, their matching tests; create `apps/web/src/lib/timeFormat.ts` and `apps/web/src/lib/timeFormat.test.ts`.

**Interfaces:** Add a pure server helper accepting a queued `datetime` and returning the UTC deadline; add `formatDurationHours(hours: number): string` returning e.g. `4.9 ч`, and use it for every measured duration. Keep the existing `sla_deadline` API field.

- [ ] Add failing backend examples: queue transition at 20:00 MSK has deadline next day 13:00 MSK; at 22:00 MSK next day 14:00 MSK; at 09:00 MSK same day 14:00 MSK; missing transition yields null deadline. Run `cd apps/api && uv run pytest tests/test_tracker_read.py -q`; expect calendar-hour/creation-time failures.
- [ ] Implement the server calculation with `ZoneInfo('Europe/Moscow')` and convert the result back to UTC. Avoid per-issue network history calls in list endpoints; document unknown start as null. Rerun targeted API tests.
- [ ] Add failing web tests: `formatDurationHours(4.94) === '4.9 ч'`, `formatDurationHours(0) === '0.0 ч'`; issue age, analytics hours, and SLA use the same formatter while an event timestamp stays `HH:mm`. Run targeted Vitest; expect current integer/day formatting to fail.
- [ ] Implement shared presentation-only formatting in `RepairSla`, `issue-utils`, `analyticsModel`, `OperationsPanels`, and `OverviewSections`. Run `rg -n 'age_hours|overdue_hours|oldest_hours|hours_created|duration' apps/web/src` to find any further measured-duration output, add those consumers to this task, and explicitly leave timestamp/threshold/interval labels unchanged. `RepairSla` uses the page-level minute tick already in `WorkPage`, with no per-card timer; overdue remains based on exact deadline. Rerun targeted tests and commit.

### Task 3: Preserve page data and avoid mutation flashes

**Files:** Modify `apps/web/src/lib/resource.ts` only if a store behavior is missing; remove unconditional route-unmount invalidations from `apps/web/src/domains/work/IssueWorkbench.tsx`, `apps/web/src/domains/shift/OverviewPage.tsx`, `apps/web/src/domains/insights/InsightsPage.tsx`, `apps/web/src/domains/analytics/AnalyticsWorkspace.tsx`, `apps/web/src/domains/robots/RobotPage.tsx`, and `apps/web/src/pages/Reports.tsx` where their access keys already isolate protected data. Update their matching tests.

**Interfaces:** Existing `useCachedResource` and `resourceStore` remain the public cache API. `invalidate` evicts on denial/auth or affected mutation; `revalidate` refreshes without clearing visible data.

- [ ] Write failing navigation tests for Work and Overview: load once, unmount/remount under the same principal/access, assert previously loaded content paints before the refetch and there is no second cold request. Write a denial test that clears protected cache on 403. Run targeted Vitest; expect the current unmount evictions to fail.
- [ ] Remove only route-unmount evictions; retain generation guards and principal/access invalidation. Where mutations currently invalidate list/detail/comments wholesale, revalidate affected mounted resources while retaining the last snapshot, with explicit pending/error state. Do not store sensitive responses on disk.
- [ ] Run focused resource/auth/navigation tests and repeated-navigation browser request counting. Verify that changed access or logout cannot show the previous principal's content. Commit the cache fix.

### Task 4: Last activity, IP, device, and approximate place

**Files:** Create `apps/api/alembic/versions/0029_user_activity.py`, `apps/api/src/robopark_api/services/user_activity.py`, `apps/api/src/robopark_api/services/ip_location.py`; modify `apps/api/src/robopark_api/models.py`, `apps/api/src/robopark_api/routers/auth.py`, `apps/api/src/robopark_api/deps.py`, `apps/api/src/robopark_api/routers/admin_users.py`, `apps/web/src/api.ts`, `apps/web/src/components/admin/AdminUsersPanel.tsx`; add focused API and web tests.

**Interfaces:** `UserAdminOut` gains nullable `last_seen_at`, `last_ip`, `last_device`, `last_location` (city/region/country); no public `UserOut` field changes. Activity is one upserted row per user. The lookup worker receives only validated public IPs, returns nullable location, and never holds an auth request open.

- [ ] Add failing API tests: login records one latest row; repeated authenticated requests within ten minutes do not write again; changed IP/device updates it; ordinary user cannot call `/admin/users`; provider timeout/private IP/429 leave login successful and location unknown. Mock only the external HTTPS transport, not the database. Run targeted pytest.
- [ ] Add the migration/model/service and bounded, sanitized user-agent handling. Throttle activity writes and provider calls; persist temporary seven-day lookup cache with expiry cleanup and cap 800/day. Implement the background lookup and null/error paths without blocking API responses.
- [ ] Add failing web tests for known/unknown metadata in user management and ensure no IP appears on unauthorised surfaces. Render concise location/IP/device/time rows in desktop and phone layouts. Rerun tests and commit.

### Task 5: Reduce visual noise, improve motion, and release verification

**Files:** Modify `apps/web/src/domains/work/TaskSyncStatus.tsx`, matching tests, relevant copy in `apps/web/src/i18n/ru.ts`, `apps/web/src/app/shell/AppShell.tsx`, `apps/web/src/components/auth/AuthLayout.tsx`, `apps/web/src/components/ScreenshotGuard/screenshotGuardLogic.ts`, and affected CSS/test files. Update release version sources only after verification.

**Interfaces:** Pending and attention states remain visible; successful sync states render no repeated badge. User-identifying watermark remains. Motion is at most 200 ms and disabled with `prefers-reduced-motion`.

- [ ] Add failing tests for no visible `Сохранено` badge in successful task rows, continued pending/error status, no redundant «Робопарк» text in shell/login/footer, and reduced-motion behavior. Run targeted Vitest and mobile visual tests.
- [ ] Remove redundant copy and adjust gaps/touch targets for 390 px phone and desktop. Add minimal opacity/transform transitions without delaying content or using layout-shifting animation. Rerun focused tests.
- [ ] Run full API and web tests, lint/typecheck, production build, migration upgrade check, 200-session capacity scenario, and mobile/desktop smoke. Record actual counts and failures. Only then bump all version sources, build and verify a signed OTA release with the existing key, and commit the release artifacts' source changes.

## Self-review

- Spec coverage: camera, working SLA, all-duration display, navigation/cache, mutation continuity, last activity/IP location, visual noise, brand, mobile motion, and signed release have tasks above.
- No task may rely on a third-party GeoIP response for the critical path.
- Exact GPS and definitive park/office presence are intentionally outside this release.
