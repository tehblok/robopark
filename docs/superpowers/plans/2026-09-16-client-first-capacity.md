# Client-first capacity implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the current СУРП screens responsive for 200 active users while reducing traffic and work on the 8-GB/Wi-Fi host; retain compatibility with an optional Orin host.

**Architecture:** Keep authority and writes on the API, but load route code on demand and hold protected responses in bounded browser memory. Replace repeated full refreshes with a small shared revision signal where measured benefit exceeds its cost; keep a bounded polling fallback. Measure both latency and bytes before accepting each stage.

**Tech Stack:** React 19, TypeScript, Vite, Vitest, FastAPI, SQLAlchemy/SQLite, nginx, pytest, existing capacity gate.

**Spec:** `docs/superpowers/specs/2026-09-16-client-first-capacity-design.md`

## Execution notes (2026-09-16)

- Baseline production bundle: initial JS 1,035.91 kB (304.90 kB gzip), CSS 161.82 kB (35.42 kB gzip). After route splitting: initial JS about 258 kB (80 kB gzip), CSS about 82 kB (19 kB gzip). These are build artifacts, not a measured phone p95.
- Protected resource responses now stay in a 128-entry in-memory cache. Startup purges all old `robopark:res:` snapshots without JSON parsing; no production screen opts into disk persistence.
- The revision signal uses small file-backed, cross-worker counters under the existing shared live-merge/data root. This avoids an OTA database migration and an extra background database loop. Royal/admin see the global work revision; other work users see the sum of revisions for their assigned parks only. Presence pings never advance it. The selected inventory park uses its park revision plus the shared catalog revision. Reports retain their existing visible-screen refresh, avoiding a global activity signal visible to narrowly scoped report users.
- Only visible, online tabs start revision checks every approximately 4–4.2 s (request time is included); failures back off to 60 s. Last-seen versions survive route unmounts in bounded tab memory. Each active tab polls independently: cross-tab leader election/SSE is deferred until a local target measurement shows a material benefit, because lease failure modes would add complexity to the 8-GB/Wi-Fi installation.
- The capacity harness now includes the revision endpoint and aggregate response bytes. A 200-user PASS, device/Wi-Fi p95, memory trend, and Tuna result require a test account and target-host run; none are inferred from the Mac tests.

## Global Constraints

- The existing Armbian 8-GB/Wi-Fi host is a required target; Orin is optional and must be measured separately.
- Screens and available actions keep their appearance and behavior; p95 visual click feedback target is 150 ms on the declared test phone.
- No bot token, integration cookie, raw Emergency payload, or authorization decision goes to the browser.
- No synthetic 200-user test against production without a new live-test window.
- Cache keys must include user/access/park scope; protected data must not persist on disk or survive logout/401/403.
- Mutations remain server-authoritative and idempotent; an optimistic state must never appear as confirmed before server response.

---

### Task 1: Measure the unchanged build and representative reads

**Files:** Update this plan's execution notes and `deploy/CAPACITY-RU.md` with local invocation and result fields; no production code.

**Interfaces:** Record initial JS/CSS bytes from Vite's production build and, when a local API and test account are available, request count/bytes and p95 navigation on an authenticated local session. Existing `scripts/capacity-gate.py` is the 200-client measurement tool; no production URL default.

- [x] Run the existing web tests: 1969/1970 passed in parallel; the 5-second inventory pagination timeout passed 13/13 in focused rerun.
- [x] Run TypeScript and Vite build from the bundled Node executable. Baseline: one 1,035.91-kB JS file (304.90 kB gzip), one 161.82-kB CSS file (35.42 kB gzip), six WebP robot assets loaded when requested.
- [ ] Record authenticated request/byte/latency baseline only on a local stand with a test account; this is not a reason to block route splitting when no local stand is running.

### Task 2: Split route code without changing route access or UI

**Files:** Modify `apps/web/src/app/routing/AppRouter.tsx`; modify `apps/web/src/app/routing/AppRouter.test.tsx`; optionally add a tiny `apps/web/src/app/routing/lazyRoute.tsx` only if one reusable error boundary is needed.

**Interfaces:** `ROUTE_ELEMENTS: Record<AppRouteId, ReactElement>` stays public. Every lazy element renders through the existing `RouteGate` and `RouteFallback`; no route path or access-policy changes.

- [ ] Add a failing test that opens `/work` and `/inventory` through `AppRouter`, waits for the lazy screen, and checks identical route-gate denial; run the focused Vitest file and observe RED.
- [ ] Replace eager page imports with `lazy(() => import('../../domains/work/WorkPage').then(m => ({ default: m.WorkPage })))` and equivalent imports for heavy pages; wrap the gated element in `<Suspense fallback={<RouteFallback />}>`.
- [ ] Run route tests GREEN, `npm run build`, inspect Vite output for a smaller initial JS chunk and no increase in total downloaded bytes on a single-route journey; commit.

### Task 3: Bound private browser data and redundant loads

**Files:** Modify `apps/web/src/lib/resource.ts`, `apps/web/src/lib/resource.test.ts`; audit callers in `apps/web/src/domains/work/IssueWorkbench.tsx`, `apps/web/src/domains/inventory`, `apps/web/src/pages/Reports.tsx`, and `apps/web/src/domains/robots`.

**Interfaces:** Existing `useCachedResource<T>(key, loader, opts)` remains compatible; `opts.persist` defaults to `false` for protected responses. `resourceStore.clearAll()` remains the logout/access-change invalidation entry point.

- [ ] Add RED tests: protected default load never writes `robopark:res:` to localStorage, returns cached data instantly on remount in the same tab, 401/403 clears the resource, and two consumers of one key share one loader.
- [ ] Change `const persist = opts.persist ?? true` to `const persist = opts.persist ?? false`; explicitly opt in only genuinely public static catalogs after audit; keep the existing 128-entry memory cap and generation guard.
- [ ] Run resource, auth, work, inventory and reports tests GREEN; run full web test/build; verify no private `robopark:res:` records remain after a representative journey; commit.

### Task 4: Add version-only invalidation across API workers

**Files:** Create `apps/api/src/robopark_api/services/change_revisions.py`, `apps/api/src/robopark_api/routers/changes.py`, `apps/api/tests/test_change_revisions.py`; modify `apps/api/src/robopark_api/main.py`, `apps/api/src/robopark_api/services/tracker_cache.py`; add the smallest necessary migration under `apps/api/alembic/versions/` only if revisions cannot safely use existing shared storage.

**Interfaces:** `mark_changed(scope: str, key: str) -> int` returns a monotonically increasing revision after a successful local write; `GET /changes?since=<revision>` returns `{revision: int, changed: list[str]}` and no data payload, with authentication and scope filtering. Coalesce changes by `(scope,key)` before notifying clients. Tracker invalidation calls `mark_changed` only after successful mutation, never on a failed attempt.

- [ ] Write RED API tests for two worker instances observing one revision, no event on failed write, no cross-park/user disclosure, bounded response, and 401/403; run focused pytest.
- [ ] Implement revision storage and a conditional `GET /changes` with an at-most-small JSON body; use a shared SQLite transaction or the existing cross-process merge store, not per-client background database loops.
- [ ] Hook successful mutation/invalidation paths, run focused API tests GREEN and full API suite; commit. If measured polling on 8 GB exceeds budget, stop here and use a shared, bounded observer to fan out version-only SSE; retain the endpoint as fallback.

### Task 5: Refresh only visible resources and coordinate tabs

**Files:** Create `apps/web/src/lib/changeFeed.ts`, `apps/web/src/lib/changeFeed.test.ts`; modify `apps/web/src/lib/resource.ts`, `apps/web/src/app/shell/AppShell.tsx`, `apps/web/src/api.ts`; add `apps/web/src/lib/changeScopes.ts` if mapping resource keys requires its own module.

**Interfaces:** One change feed per authenticated browser context, `startChangeFeed({ userId, parkId, onKeys }): () => void`; `onKeys` invalidates only mounted resource keys. Feature-detect BroadcastChannel and share changed keys with same-account tabs; hidden tabs do not fetch data. The feed backs off with jitter, resumes on visibility/online, and clears itself on auth change.

- [ ] Write RED tests for repeated events coalescing to one refresh, out-of-scope events ignored, hidden/offline suspension, reconnect catch-up, two-tab broadcast, and 401/403 teardown.
- [ ] Implement a version-only fetch through the existing authenticated `request` helper; schedule no faster than needed to meet the healthy-channel 5-second freshness target and never fetch full resources when their screen is not mounted. Add SSE only after local tunnel simulation shows it uses fewer resources than version polling; nginx stream buffering must be disabled for that route if enabled.
- [ ] Run focused tests GREEN, full web tests/build, and integration tests with two API workers; commit.

### Task 6: Verify 8-GB/Wi-Fi capacity, fallback, and optional photo work

**Files:** Extend `scripts/capacity-gate.py` and `deploy/CAPACITY-RU.md` with the measured route/read mix; add a targeted Playwright scenario under `apps/web/e2e/` for offline/stale/online and one photo upload only if photo bytes dominate the baseline.

**Interfaces:** Capacity output records role/park mix, active/hidden tab mix, requests/s, bytes/s, p95 API and UI, CPU/RSS, SQLite contention, tunnel disconnects, and 30-minute post-warmup memory trend. `--base-url` must be explicitly supplied; production is never a default.

- [ ] Add RED tests for the new capacity report schema and stale/offline visual state; run focused tests.
- [ ] Extend the script and scenario, compare before/after on a local 8-GB/Wi-Fi-equivalent setup; if photo optimization is justified, test readable defect detail before applying client preview/downscale.
- [ ] Run host/API/web suites and the local capacity gate, report measured limits honestly, repeat on Orin only if available, and commit. Release/OTA packaging is a separate step after all gates pass.
