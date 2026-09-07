# Task 9 report — Royal system health and OTA controls

Implemented in `codex/armbian-installer-task9` worktree only. No subagents spawned.

## Delivered

- Exact Task 7 public contracts: seven SystemHealth fields, nullable version/Git/generated timestamp, CheckOut repair, UpdateOut state/publication, BackupOut status/completed_at, inspection metadata and HostResultOut. Added all eight health/discovery/inspection/approval/diagnostic/repair/artifact API methods, with strict JSON approval payloads.
- Task 8 available-update is a separate resource with the agreed five states and host-discovered release metadata. No arbitrary release URL input.
- System health uses the shared stale-while-revalidate resource store (memory only), focus/visible refresh, last good data on transient failure, visible Russian status with SVG icons, version/short Git SHA, check/backup ages, stale data, publication degradation and rollback notice.
- Explicit repair action and translated performed/failed outcomes. Diagnostics progress and download only when the current job is completed diagnostics with an artifact. Snapshot artifacts have their own equivalent gating.
- ZIP inspection happens on file selection, with version/Git/migration/notes preview. Installation requires exact `ОБНОВИТЬ`. Selecting another file clears inspection and confirmation; request generations ignore late inspection results.
- GitHub installation requires the existing accessible Dialog and exact confirmation. It submits only host release ID and confirmation. Actual `github_release_unavailable` HTTP 400 refreshes discovery; conflicts refresh health/discovery/job. Russian mappings include confirm_required, github_approval_actor_mismatch, host_work_in_progress, job_in_progress and host_operation_failed.
- Only active jobs poll, initially after 2.5 seconds; transient failures back off to 5/10/20/30 seconds. Polling pauses when hidden, resumes on focus/visibility, and stops on terminal state/unmount. Polling keeps the last good status, coalesces requests, and guards stale responses after dispatch. No abort or manual refresh controls.
- Existing design-system Button, Dialog, StatusBadge, LoadingState, Panels and token colors; no emoji or raw logs/JSON/traceback/path output. Narrow layouts wrap metadata and controls; reduced motion is inherited from the design system.
- Admin settings tab group now uses existing design-system Tabs with labelled lazy panels, Home/End/arrow focus behavior. The change is localized to this group and keeps operations unmounted while another settings tab is selected.

## TDD evidence

1. Initial component RED: missing SystemHealthPanel and eight failing AdminOps interactions (no inspection/approval/health/diagnostics/repair behavior; hidden polling continued). API RED: opsSystemHealth did not exist.
2. Initial GREEN: 12 focused component/API tests.
3. Exact Task 8 contract RED: seven failures for actual HTTP 400 stale-release recovery and Russian error codes; fixed to GREEN.
4. Duplicate-fetch RED: mounting an already completed job caused two health loads. Fixed to refresh on a live terminal transition/action result, not initial completed-job discovery.
5. Browser axe RED: legacy buttons/alerts failed contrast (3.37:1 / 4.25:1). Converted operations controls and alerts to design-system components/tokens.
6. Browser axe RED: old selected Admin tab failed 3.37:1 contrast. Focused Admin keyboard RED: End did not move focus or label a panel. Converted only Admin tab group to DS Tabs and lazy labelled panels; GREEN.

## Final validation

All commands below ran from `apps/web` after the final production changes:

- Focused component/API/error/management tests: **49 passed, 6 files**.
  `npm test -- src/components/admin/AdminOpsPanel.test.tsx src/components/admin/SystemHealthPanel.test.tsx src/opsApi.test.ts src/api.test.ts src/i18n/errors.test.ts src/domains/management/ManagementPage.test.tsx`
- Full web suite: **1311 passed, 85 files** (`npm test`).
- TypeScript + Vite production build: **PASS** (`npm run build`). Vite still reports its existing large-chunk advisory; no build failure.
- Navigation: **PASS**, 27 route IDs (`npm run check-nav`).
- Token contrast: **PASS**, both themes (`npm run check:contrast`).
- Changed production component/hooks/Admin lint: **PASS**, no warnings.
- Browser E2E: **4 passed** (`npm run test:e2e -- e2e/operational/ops.spec.ts`). Chromium checks real settings route at 320/1440 px in light/dark themes, reduced motion, no horizontal page overflow, full-page WCAG AA axe checks, exact confirmation gating, dialog keyboard focus and Escape focus return.
- Screenshots visually inspected: `/tmp/task9-320-light.png` and `/tmp/task9-1440-dark.png`; all four viewport/theme captures are in `/tmp/task9-{width}-{theme}.png`.
- `git diff --check`: **PASS**.

Browser execution required an automatically approved sandbox escalation because the default sandbox prohibits the local Vite listening socket. Browser tests use controlled API fixtures; actual host deployment/OTA execution is outside this frontend task. The API exposes rollback state but no detailed rollback reason, so the UI uses a safe general failure/rollback explanation and diagnostic action.
