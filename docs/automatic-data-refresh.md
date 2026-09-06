# Automatic data refresh

Users read live data without a manual refresh action. Operational resources use
30-second freshness and refresh intervals; historical analytics and reference
catalogs can use two minutes. Robot telemetry uses a 10-second base interval for both the snapshot and the
selected section. Maintenance status uses the shared 30-second cadence (previously
it issued a request every two seconds from every tab). Identical in-flight requests share one result, while fresh cached data
can render immediately on return visits. Existing API Tracker/Emergency caches
continue to merge upstream requests across users.

Automatic refresh pauses offline or in hidden browser tabs and resumes when the
page is visible/online. Reconnect/focus requests are spread over 0–30 seconds;
repeated lifecycle events coalesce. Cold visible/online mounts and explicit
actions still load immediately. Periodic requests add positive 0–20% jitter;
the first background interval adds 0–100% to spread synchronized starts. Thus
robot background intervals are initially 10–20 seconds, then 10–12 seconds;
operational resources are initially 30–60 seconds, then 30–36 seconds. Requests do not overlap; failures back off, authentication
or access denial stops automatic attempts. HTTP 429 is a transient failure,
retains last-known telemetry/session state, and automatic retries honor
`Retry-After` seconds or HTTP dates in addition to exponential backoff. User
actions are never automatically replayed. Background updates retain useful
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


## Capacity envelope — 200 visible users

The busiest normal check view is the robot check embedded in an issue workbench:

| Read | Base requests per user per second |
| --- | ---: |
| Robot snapshot + selected section | 2 / 10 |
| Work list + issue detail + comments + allowed transitions | 4 / 30 |
| Maintenance status | 1 / 30 |
| Auth `/me` | 0 periodic; bootstrap, login or explicit refresh only |

This is at most **73.33 steady requests/second for 200 users**, or about 66.67
using the mean jittered intervals, before network latency. The standalone robot
page needs snapshot/section, related tickets and maintenance: at most 53.33 RPS.
The issue's related-tasks panel and robot-check panel are mutually exclusive.
These are read-only steady-state estimates, not a throughput guarantee: cold
starts, navigation, writes, attachment traffic and other clients share the same
Tuna domain quota. Two hundred simultaneous cold starts can still receive 429;
backoff and jitter recover without replaying mutations or expiring valid sessions.
A faster Ubuntu host does not increase the tunnel's domain request quota.

Fake-clock tests cover phase dispersion for 200 robot viewers, pause/resume,
coalescing, Retry-After, preserved sessions/drafts and immediate manual actions.
Chromium robot checks exercise deterministic nonzero jitter; marker layout tests
use minimum jitter and advance freshness by more than ten seconds.
