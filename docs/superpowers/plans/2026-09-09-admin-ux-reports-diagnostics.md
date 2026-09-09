# Admin UX, Reports, and Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a signed 0.1.18 OTA with collapsible large panels, host-phase update progress, correct royal report visibility, an administration park multi-select, true diagnostic-error suppression, and no permanent wheel hotspots.

**Architecture:** Extend existing boundaries instead of creating new subsystems: panel primitives own collapse state, a focused `ParkMultiSelect` owns administration selection UX, report services keep role routing, diagnostic projection filters stored ignored identities, and host bridge projects durable update phases. Each behavior is covered by a narrow red-green test before integration verification.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, pytest, React 19, TypeScript 6, Vitest, Testing Library, Docker/host updater, Ed25519 release tooling.

**Spec:** `docs/superpowers/specs/2026-09-09-admin-ux-reports-diagnostics-design.md`

## Global Constraints

- Preserve the current single-park context outside administration.
- Avoid a database migration; reuse `DiagnosticUnknown.state == "ignored"`.
- Derive OTA progress only from durable, allow-listed host phases.
- Royal sees all reports across all parks and parkless system reports.
- Real diagnostic markers remain; permanent wheel hotspots are removed.
- Release 0.1.18 must install over 0.1.17 and use the active signing key.

---

### Task 1: Reusable collapsible panels

**Files:**
- Modify: `apps/web/src/design-system/layout/PageLayout.tsx`
- Modify: `apps/web/src/design-system/layout/PageLayout.css`
- Modify: `apps/web/src/design-system/layout/PageLayout.test.tsx`
- Modify: `apps/web/src/components/PageShell.tsx`
- Modify: `apps/web/src/index.css`
- Test: `apps/web/src/design-system/layout/PageLayout.test.tsx`
- Test: `apps/web/src/components/PageShell.test.tsx`

**Interfaces:**
- Produces: `collapsible?: boolean`, `storageKey?: string`, and `defaultCollapsed?: boolean` panel props.
- Storage key: `robopark:panel:<storageKey>:collapsed`; only the string `"1"` means collapsed.

- [ ] **Step 1: Add failing tests for accessible collapse and persistence**

```tsx
render(<Panel collapsible storageKey="diagnostics" title="Диагностика"><p>Данные</p></Panel>)
fireEvent.click(screen.getByRole('button', { name: 'Свернуть: Диагностика' }))
expect(screen.queryByText('Данные')).not.toBeInTheDocument()
expect(localStorage.getItem('robopark:panel:diagnostics:collapsed')).toBe('1')
```

- [ ] **Step 2: Run the focused tests and confirm they fail because the props/button do not exist**

Run: `cd apps/web && npm test -- src/design-system/layout/PageLayout.test.tsx src/components/PageShell.test.tsx`

- [ ] **Step 3: Implement the same explicit collapse contract in both panel primitives**

Use `useState` with guarded `localStorage` reads/writes, `aria-expanded`, `aria-controls`, localized `Свернуть: <title>` / `Развернуть: <title>` labels, and conditionally render the content. Reject `collapsible` without a nonempty title and storage key in development tests rather than deriving identity from visible text.

- [ ] **Step 4: Add focused styles for the disclosure button and collapsed header**

Keep the existing header/actions layout and use the existing button/icon language; do not animate height or measure content.

- [ ] **Step 5: Run focused tests**

Run: `cd apps/web && npm test -- src/design-system/layout/PageLayout.test.tsx src/components/PageShell.test.tsx`

Expected: PASS.

### Task 2: Apply collapse only to large screens

**Files:**
- Modify: `apps/web/src/pages/Admin.tsx`
- Modify: `apps/web/src/pages/AdminEmergencyConfig.tsx`
- Modify: `apps/web/src/pages/AdminTrackerWorkspace.tsx`
- Modify: `apps/web/src/components/admin/AdminOpsPanel.tsx`
- Modify: `apps/web/src/components/admin/AdminRolesPanel.tsx`
- Modify: `apps/web/src/components/admin/AdminUsersPanel.tsx`
- Modify: `apps/web/src/domains/diagnostics/DiagnosticRuleEditor.tsx`
- Modify: `apps/web/src/pages/Reports.tsx`
- Modify: `apps/web/src/domains/work/IssueWorkbench.tsx`
- Test: corresponding existing `*.test.tsx` files.

**Interfaces:**
- Consumes: Task 1 panel props.
- Produces: stable, unique storage keys such as `admin-ops-update`, `admin-users-list`, `reports-mine`, and `work-detail`.

- [ ] **Step 1: Add one failing integration assertion per panel family**

Assert that large administration, diagnostics, reports, and work panels expose their named collapse control while metric/identity/status panels do not.

- [ ] **Step 2: Run the selected page tests and confirm missing controls fail**

Run: `cd apps/web && npm test -- src/pages/Admin.test.tsx src/pages/Reports.test.tsx src/domains/diagnostics/DiagnosticRuleEditor.test.tsx src/domains/work/IssueWorkbench.test.tsx`

- [ ] **Step 3: Opt in only panels with forms, unbounded lists, logs, or large detail content**

Do not mark `MetricCard`, health summaries, warnings, robot identity, or other small read-only cards collapsible.

- [ ] **Step 4: Re-run the selected page tests**

Expected: PASS without changing existing default-visible assertions.

### Task 3: Administration park multi-select

**Files:**
- Create: `apps/web/src/components/admin/ParkMultiSelect.tsx`
- Create: `apps/web/src/components/admin/ParkMultiSelect.test.tsx`
- Modify: `apps/web/src/components/admin/AdminOpsPanel.css`
- Modify: `apps/web/src/components/admin/AdminUsersPanel.tsx`
- Modify: `apps/web/src/components/admin/AdminUsersPanel.test.tsx`
- Modify: `apps/web/src/pages/Admin.test.tsx`

**Interfaces:**
- Produces: `ParkMultiSelect({ parks, value, onChange, disabled?, label })`, where `value: number[]` and `onChange(next: number[]): void`.
- Preserves inactive parks already assigned; disallows newly selecting inactive parks.

- [ ] **Step 1: Write failing component tests**

Cover opening the dropdown, checkbox selection, text filtering, selected count, `Выбрать все`, `Очистить`, Escape, outside click, keyboard focus, disabled state, and retained inactive selections.

- [ ] **Step 2: Run the new test and verify failure due to the missing component**

Run: `cd apps/web && npm test -- src/components/admin/ParkMultiSelect.test.tsx`

- [ ] **Step 3: Implement the controlled dropdown without changing API payloads**

Use the existing `Park` type and return sorted unique numeric IDs. Keep the popup in the DOM only while open and expose `role="listbox"` with `aria-multiselectable="true"`.

- [ ] **Step 4: Replace both user edit/create checkbox grids**

Bind edit to `draft.park_ids` and creation to `createForm.parkIds`; delete obsolete toggle helpers only after tests use the new contract.

- [ ] **Step 5: Run component and admin user tests**

Run: `cd apps/web && npm test -- src/components/admin/ParkMultiSelect.test.tsx src/components/admin/AdminUsersPanel.test.tsx src/pages/Admin.test.tsx`

Expected: PASS and existing create/update payload assertions retain identical `park_ids` arrays.

### Task 4: Explicit report routing for every role

**Files:**
- Modify: `apps/api/src/robopark_api/services/reports.py`
- Modify: `apps/api/tests/test_reports.py`
- Modify: `apps/api/tests/test_report_permissions.py`
- Modify: `apps/web/src/app/routing/AppRouter.test.tsx`
- Modify: `apps/web/src/app/shell/AppShell.test.tsx`

**Interfaces:**
- `list_inbox(db, royal, park_id=None)` returns every open report in global scope.
- `badge_counts(db, royal, park_id=None)` counts all open reports plus royal-authored returned reports without double-counting.
- Admin/operator/mechanic/driver behavior remains as specified.

- [ ] **Step 1: Add failing API matrix tests**

Seed two parks, operator-targeted reports, admin escalations, a parkless stale-cookie report, returned author reports, and a closed report. Assert exact inbox/detail/action/badge results for royal, admin, operator, mechanic, and driver.

- [ ] **Step 2: Run report tests and confirm royal misses operator-targeted rows**

Run: `cd apps/api && uv run pytest tests/test_reports.py tests/test_report_permissions.py -q`

- [ ] **Step 3: Split royal from admin inbox logic**

Add `_is_royal_inbox_user`; for royal use open status plus global scope without a target-role predicate. Keep admin restricted to `target_role == ADMIN`, operator restricted to `target_role == OPERATOR`, and author visibility unchanged. Build badge predicates with `or_` so one report is counted once.

- [ ] **Step 4: Add the stale-cookie automatic report assertion**

Call `ensure_open_emergency_cookie_report`, then assert royal inbox and badge include it while operator/mechanic/driver do not.

- [ ] **Step 5: Add frontend regression tests for royal navigation, inbox, and badge**

Mock mixed-target report responses and assert the royal reports link badge renders the returned count and the reports route renders both target types.

- [ ] **Step 6: Run focused API and web tests**

Run: `cd apps/api && uv run pytest tests/test_reports.py tests/test_report_permissions.py -q`

Run: `cd apps/web && npm test -- src/app/routing/AppRouter.test.tsx src/app/shell/AppShell.test.tsx`

Expected: PASS.

### Task 5: Ignored diagnostics disappear from robot output

**Files:**
- Modify: `apps/api/src/robopark_api/services/diagnostic_rules.py`
- Modify: `apps/api/src/robopark_api/services/diagnostic_unknowns.py`
- Modify: `apps/api/src/robopark_api/routers/admin_diagnostic_unknowns.py`
- Modify: `apps/api/tests/test_diagnostic_rules.py`
- Modify: `apps/api/tests/test_diagnostic_unknowns.py`
- Modify: `apps/api/tests/test_emergency.py`
- Modify: `apps/web/src/domains/diagnostics/UnknownDiagnosticInbox.tsx`
- Modify: `apps/web/src/domains/diagnostics/UnknownDiagnosticInbox.test.tsx`

**Interfaces:**
- Add `ignored_diagnostic_identities(db) -> set[str]`.
- `match_diagnostic_events(db, payload)` filters only unmatched events whose deterministic `event.id` is ignored.
- `match_diagnostic_events_for_rules(rules, payload)` remains unchanged for admin preview tests.

- [ ] **Step 1: Write a failing projection test**

Create an ignored unknown with the exact identity produced for a raw payload and assert `match_diagnostic_events(db, payload)` omits it while a mapped rule event remains.

- [ ] **Step 2: Run focused diagnostic tests and confirm the ignored raw event remains visible**

Run: `cd apps/api && uv run pytest tests/test_diagnostic_rules.py tests/test_diagnostic_unknowns.py tests/test_emergency.py -q`

- [ ] **Step 3: Filter ignored identities at the database-aware projection boundary**

Query only `DiagnosticUnknown.identity` where state is `ignored`; never pass ignored events to `capture_unknowns`. Keep preview matching independent of database suppression.

- [ ] **Step 4: Invalidate the raw emergency cache after ignore/reopen**

Call the existing cache invalidation boundary after a successful state transition so the next robot check re-fetches and re-projects promptly. Do not expose robot IDs or raw values in the response.

- [ ] **Step 5: Change administration language and tests**

Use labels `Игнорировать`, `Игнорируемые`, and `Вернуть`; success text says the error is hidden from robot checks until restored.

- [ ] **Step 6: Re-run focused API and web tests**

Run: `cd apps/api && uv run pytest tests/test_diagnostic_rules.py tests/test_diagnostic_unknowns.py tests/test_emergency.py -q`

Run: `cd apps/web && npm test -- src/domains/diagnostics/UnknownDiagnosticInbox.test.tsx`

Expected: PASS.

### Task 6: Remove permanent wheel hotspots

**Files:**
- Modify: `apps/web/src/domains/robots/RobotDiagnosticDiagram.tsx`
- Modify: `apps/web/src/domains/robots/RobotCheckWorkspace.tsx`
- Modify: `apps/web/src/domains/robots/robot-check.css`
- Modify: `apps/web/src/domains/robots/RobotCheckWorkspace.test.tsx`
- Delete if unused: `apps/web/src/domains/robots/robotPhotoHotspots.ts`
- Delete if unused: `apps/web/src/domains/robots/robotPhotoHotspots.test.ts`

**Interfaces:**
- Removes `onSelectWheels` from `RobotDiagnosticDiagram`.
- Retains `faults` only for truthful textual wheel-fault summary if needed.
- Retains all `DiagnosticEvent` marker selection and reveal behavior.

- [ ] **Step 1: Rewrite the existing wheel test to require no wheel buttons**

Assert no button accessible name contains `колесо`, then provide a real localized diagnostic event and assert its `Ошибка: <title>` marker remains clickable.

- [ ] **Step 2: Run the focused test and confirm it fails because wheel buttons still render**

Run: `cd apps/web && npm test -- src/domains/robots/RobotCheckWorkspace.test.tsx`

- [ ] **Step 3: Remove hotspot rendering, selection state, navigation callback, and dead CSS/data helpers**

Keep the fallback SVG/image and diagnostic marker rendering unchanged. Preserve unlocalized fault text without an interactive circle.

- [ ] **Step 4: Re-run robot workspace tests**

Expected: PASS with real diagnostic markers still covered.

### Task 7: Durable OTA progress projection and display

**Files:**
- Modify: `apps/api/src/robopark_api/services/ops/host_bridge.py`
- Modify: `apps/api/src/robopark_api/routers/admin_ops.py`
- Modify: `apps/api/tests/test_ops_host_bridge.py`
- Modify: `apps/api/tests/test_ops_host_review.py`
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/components/admin/opsPresentation.ts`
- Modify: `apps/web/src/components/admin/opsPresentation.test.ts`
- Modify: `apps/web/src/components/admin/AdminOpsPanel.tsx`
- Modify: `apps/web/src/components/admin/AdminOpsPanel.css`
- Modify: `apps/web/src/components/admin/AdminOpsPanel.test.tsx`

**Interfaces:**
- Public `OpsJob` adds `progress_percent: number | null` and `progress_phase: string | null`.
- API accepts a host phase only when `host-status.json.job_id == job.id`, state is `updating`, and phase belongs to the updater's public allow-list.
- Web `updateProgress(job)` returns `{ percent, label, rollback }` or `null`.

- [ ] **Step 1: Add failing host-bridge tests**

Cover matching job phase, mismatched job ID, malformed status, unknown phase, forward phase, rollback phase, and unavailable status. Assert no untrusted fields reach JSON.

- [ ] **Step 2: Run focused API tests and confirm progress fields are absent**

Run: `cd apps/api && uv run pytest tests/test_ops_host_bridge.py tests/test_ops_host_review.py -q`

- [ ] **Step 3: Implement public phase projection and milestone mapping**

Use a local constant mapping for all updater forward/rollback phases. Return `null` for non-update jobs or unknown input. Do not overwrite the durable local job state with an unvalidated phase.

- [ ] **Step 4: Add failing web milestone tests**

Assert monotonic percentages for `validating`, `unpacking`, `building`, `smoking`, `snapshotting`, `publishing`, `migrating`, `starting`, and `health_check`; assert rollback gets an explicit label/tone.

- [ ] **Step 5: Render the progress bar in the current-operation panel**

Use `<progress max={100} value={percent}>`, visible percentage text, localized phase label, and `aria-live="polite"`. Preserve polling/backoff and the existing abort action.

- [ ] **Step 6: Run focused API and web tests**

Run: `cd apps/api && uv run pytest tests/test_ops_host_bridge.py tests/test_ops_host_review.py -q`

Run: `cd apps/web && npm test -- src/components/admin/opsPresentation.test.ts src/components/admin/AdminOpsPanel.test.tsx`

Expected: PASS.

### Task 8: Integration, version, and signed OTA

**Files:**
- Modify: `VERSION`
- Modify: `apps/api/pyproject.toml`
- Modify: `apps/api/uv.lock`
- Modify: `apps/api/src/robopark_api/services/ops/context.py`
- Modify: `apps/web/package.json`
- Modify: `apps/web/package-lock.json`
- Modify: `deploy/release-metadata.json`

**Interfaces:**
- Produces version `0.1.18` in every canonical version source.
- Produces `artifacts/robopark-0.1.18-<sha>/robopark-release-0.1.18.zip` and its detached signature.

- [ ] **Step 1: Run all focused suites together**

Run the exact focused commands from Tasks 1-7 and resolve only regressions caused by this change.

- [ ] **Step 2: Update all canonical version sources and release notes to 0.1.18**

Use the existing version check script to prove consistency:

Run: `python3 scripts/check-release-version.py`

- [ ] **Step 3: Run the full API/host suite**

Run: `cd apps/api && uv run pytest -q`

Run: `uv run pytest tests/host -q`

- [ ] **Step 4: Run the full web suite and production build**

Run: `cd apps/web && npm test`

Run: `cd apps/web && npm run build`

- [ ] **Step 5: Inspect the final diff and commit only task files**

Run: `git diff --check`

Run: `git status --short --untracked-files=all`

Preserve the pre-existing `.pnpm-store` entries and `docs/emergency-api-migration.md` without staging them.

- [ ] **Step 6: Push the implementation commit to `main`**

Run: `git push origin main`

- [ ] **Step 7: Read the local safety policy before accessing signing material**

Read `~/.stefania/agent-core/references/safety.md` if installed. Never print private key contents or secret environment values.

- [ ] **Step 8: Build and verify the signed release**

Use `.worktrees/armbian-installer-ota/.release-secrets/robopark-release-key.pem` as `ROBOPARK_SIGNING_KEY_FILE`, invoke `scripts/pack-release.sh` with the explicit artifact path, verify with the public key derived from that same active key, inspect manifest version/SHA, and calculate SHA-256 with `shasum -a 256`.

- [ ] **Step 9: Deliver the clickable OTA path, commit SHA, test counts, and archive SHA-256**

Do not claim completion until every command in Steps 2-8 has exited successfully and its output has been read.
