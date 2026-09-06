# Automatic data refresh

Users read live data without a manual refresh action. Operational resources use
30-second freshness and refresh intervals; historical analytics and reference
catalogs can use two minutes. Existing robot telemetry polling retains its own
cadence. Identical in-flight requests share one result, while fresh cached data
can render immediately on return visits. Existing API Tracker/Emergency caches
continue to merge upstream requests across users.

Automatic refresh pauses offline or in hidden browser tabs and resumes when the
page is visible/online. Requests do not overlap; failures back off, authentication
or access denial stops automatic attempts. Background updates retain useful
last-known data and do not repeatedly animate the global progress bar. Mutations
still force a refresh. Error retry, credential replacement, saving and software
update actions retain their separate meanings.

Editable forms must preserve unsaved input. Their draft-loading bootstrap opts
out of automatic replacement; read-only lists refresh independently. Cache keys
must include existing user/access/park/robot/query ownership and continue to be
invalidated when the session or scope changes.

## Implementation and verification plan

- [x] Extend `apps/web/src/lib/resource.ts` with freshness, automatic lifecycle,
  shared requests and failure backoff. Test timers, visibility, online resume,
  fresh cache reuse, in-flight deduplication, invalidation and access denial.
- [x] Remove regular data refresh controls from operational/legacy pages and
  analytics; migrate independent historical loaders to the shared resource.
- [x] Preserve robot polling while sharing snapshots and selected sections;
  remove robot refresh controls and duplicate EmergencyViewer timers.
- [x] Audit management/diagnostic catalogs and preserve editable drafts.
- [x] Run focused tests, full API/web/Chromium gates, build, lint, navigation and
  contrast checks. Inspect live UI without mutating external records.

## Navigation follow-up

The bottom navigation labels are always visible below their icons (14px). The
column flex rule previously collapsed their height to zero. Layout checks now
verify each label has nonzero dimensions, sits below its icon and remains within
its target. Robot and robot-check glyphs use a wheeled platform without a face,
through the existing shared icon API in all UI locations. Role-specific routes
and navigation ordering are unchanged.

## Verified results — 2026-09-06

- API: 1075 tests passed (backend behavior unchanged by this feature).
- Web: 1550 tests passed across 97 files; production build, lint, 27-route
  navigation and light/dark contrast checks completed.
- Chromium: full 199-case run passed 197, with only the two older shell image
  baselines differing due to the requested icon/label change. Those two baselines
  were updated and their cases rerun successfully. All 46 responsive visual cases
  passed, including the new label geometry checks at 320–1440px.
- Browser regression verifies incoming task text/comments update without losing
  an unsent comment. Tests also cover hidden/offline pause, cached mount reuse,
  retry backoff, shared authorization failures, deferred first loads, preserved
  pagination, mutation races and drafts in account/role/diagnostic editors.
- Live local task SDCFLEETOPS-370188 loads actual data, retains task navigation,
  and displays the labeled bottom bar with the replacement robot glyph.
