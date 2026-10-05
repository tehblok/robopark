# Form readiness and reconnect verification

## Assistant first load

The composer and New action were usable while the initial conversation list was
pending. Opening the first returned conversation subsequently disabled the field;
submission before that response could create another conversation instead of using
the existing one. Deferred-list and failed-list regressions both failed against
the old behavior. The controls and submit handler now require a successfully
loaded conversation list. A failed list stays recoverable via the existing Retry
action; opening a detail retains its independent pending guard.

The Chromium CI failure on the sourced-answer journey had an empty draft and
disabled Send button. Its original run did not retain a trace, so its exact event
ordering is not proven. The readiness regressions cover a concrete adjacent race;
they are not presented as proof of the old incident's cause. All browser suites
now retain failure traces even when the Docker wrapper runs without CI retries.

## Robot reconnect

The old scenario advanced the full 30-second reconnect jitter window and checked
only a request-start counter plus the old robot-offline label. A controlled WebKit
probe with zero jitter and a two-second mock response delay passed that assertion
even though its trace showed a cancelled snapshot request. Requiring a fresh HTTP
200 response made the same probe fail. This reproduces the test's false-positive
boundary and the interaction with the 30-second request timeout.

The scenario now stops virtual-time advancement when the reconnect request starts,
then awaits its successful response and the rendered status. It exercises zero,
midpoint, and near-maximum jitter with delayed I/O. Offline request suppression and
the distinction between browser-offline and robot-offline remain asserted.
Production polling/backoff and HTTP deadlines are unchanged.

## Inventory test synchronization

Seven legacy write-off tests selected a part synchronously after changing the
component, although inventory search is asynchronous. They now await the actual
part selector. Write-off, confirmation, hydration, and idempotency assertions are
unchanged. This includes the remaining rc.30 CI failure after the earlier selector
fix covered only one test.

## Verification

- Two new assistant regressions were observed failing before their fixes.
- Assistant and IssueWorkbench unit suites: 181 passed.
- Assistant journeys plus three reconnect jitter cases across Chromium, Firefox,
  and WebKit in the pinned Linux image: 21 passed.
- Frontend route lint and production TypeScript/Vite build passed.
- No live-host deployment, long soak, or AGX hardware acceptance was performed.

## Follow-up

Review identified a separate candidate race while automatically creating an
issue-scoped conversation: New may remain available during that request. It needs
a deferred-create regression and independent fix; it is not covered by the
initial-list readiness change. Full CI still gates publication.
