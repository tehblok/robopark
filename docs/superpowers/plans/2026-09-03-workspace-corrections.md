# Workspace Corrections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct navigation, queue interaction and the unified robot workspace without weakening existing access/race protections.

**Architecture:** Keep the route manifest as navigation authority. Work uses one normalized oldest-first URL contract. Compose robot information and checks under one principal/scope owner and retain backward-compatible addresses.

**Tech Stack:** React, React Router, TypeScript, CSS, Vitest, Playwright.

**Spec:** docs/superpowers/specs/2026-09-03-operations-corrections-design.md (Рабочее пространство; other subsystems are covered by separate plans).

## Global Constraints

- Working directory: `/Users/tehblokdan/Desktop/Проекты/robopark/.worktrees/robopark-redesign`.
- No `.env`, secrets, live APIs/databases, dependency installation, destructive commands, main merge or push.
- Use existing runtime `/Users/tehblokdan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node` with repository CLI files.
- Preserve theme, 44px mobile targets, accessible names, keyboard interaction, scoped authorization, errors and stale-response guards.
- Oldest-first queue ordering is mandatory, including legacy URL input. A requested filter never grants access.
- Other agents may inspect sources; do not revert unrelated changes or dispatch subagents.

### Task 1: Shell navigation and queue workflow

**Files:**
- Modify: `apps/web/src/app/shell/AppShell.tsx`, `AppShell.css`, `AppShell.test.tsx`.
- Modify if needed: `apps/web/src/app/routing/routeManifest.ts` and its navigation tests.
- Modify: `apps/web/src/domains/work/workUrl.ts`, `workData.ts`, `WorkFilters.tsx`, `IssueWorkbench.tsx`, `WorkPage.tsx`, `work.css` and adjacent tests.
- Test: existing shell/work tests; add focused browser interaction checks under `apps/web/e2e` if CSS needs browser verification.

**Interfaces:**
- Consumes `NavigationItem`, current `location.pathname`, `WorkUrlState`, existing issue detail and list APIs.
- Produces stable parent-section navigation and oldest-first `parseWorkUrl`, `buildWorkSearch`, `loadWorkPage`; external signatures stay compatible.

- [ ] Write regressions before code. For nested `/work/ROBOPARK-1`, the Work navigation link must carry `aria-current=page`; for `/robots/VIN/check`, Robots must be active; for nested administration only the most specific visible navigation entry is active. Verify secondary mobile route keeps More highlighted after closing its sheet.

```tsx
expect(within(desktopNavigation).getByRole('link', { name: 'Работа' }))
  .toHaveAttribute('aria-current', 'page')
expect(parseWorkUrl(new URLSearchParams('sort=newest'), { queue: 'ROBOPARK' }).sort)
  .toBe('oldest')
```

- [ ] Run focused Vitest to observe these assertions fail for the intended missing behavior.
- [ ] Implement specific navigation ownership by matching segment boundaries and choosing the longest available route, rather than making every NavLink exact. Keep a single current entry per surface. Pin shell without trapping content or breaking Work scroll restoration. Simplify status selection to named choices, remove the newest sort choice, force oldest sorting both in URL normalization and API query construction. Keep the queue/detail interface free of overview metric panels.
- [ ] Implement remaining-task access from a selected issue using its known robot identity and existing permission-scoped API or robot-filtered Work navigation. Do not invent VIN from arbitrary text; provide an honest unavailable state when identity is absent.
- [ ] Run affected shell/work/navigation suites. Verify no mixed-scope stale updates were introduced. Review diff and commit only owned files.

### Task 2: Unified robot workspace and cache release

**Files:**
- Modify: `apps/web/src/domains/robots/RobotPage.tsx`, `RobotCheckPage.tsx`, `RobotCheckWorkspace.tsx`, `RobotDetailView.tsx`, `RobotIdentityCard.tsx`, `robotDetailData.ts`, `robots.css`, adjacent tests.
- Modify if needed: `apps/web/src/app/routing/AppRouter.tsx` and robot URL helpers.
- Modify: `apps/web/src/domains/work/IssueWorkbench.tsx` and `IssueWorkbench.test.tsx` for full-remount scope release.

**Interfaces:**
- Consumes existing `emergencyResolve`, `emergencySnapshot`, role-aware related-ticket APIs and `RobotCheckWorkspace` section/tab contracts.
- Produces one user-facing robot screen on `/robots/:vin`; `/robots/:vin/check` remains a compatible entry into the same screen, preserving park/tab. Keep injection seams used by tests or replace with explicit compatible seams.

- [ ] Add integration tests showing one robot screen exposes identity, related tasks and permitted diagnostics; switching diagnostics does not navigate away to a separate card. Existing `/check?park=8&tab=scheme` still selects the photo diagram. User without check permission does not issue diagnostic requests.
- [ ] Add A → unmount → B → unmount → A test: pending first-A detail resolves after departure, returning to A requests fresh detail, unchanged mounted scope retains caching. Run tests and observe failures.
- [ ] Compose existing robot building blocks under shared scope ownership. Keep one identity resolution and avoid duplicate snapshot loading. Preserve 401/403 ownership boundaries and route canonicalization. Add release invalidation/generation protection to Work resources across complete unmount, including pending responses, without disabling all useful caching.
- [ ] Run robot/work focused tests plus TypeScript and lint; review diff and commit owned files.

## Integration acceptance

- [ ] Review both tasks for spec compliance and code quality, fixing concrete important findings.
- [ ] Run full web unit suite, build, nav and contrast checks once after the combined changes. Browser-check fixed panels, oldest-first queue/back navigation, robot entry and More at desktop/mobile widths.
- [ ] Record results and any remaining limitation in the durable status report; never call pending metric/admin work complete.
