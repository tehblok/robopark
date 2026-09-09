# Admin UX, Report Routing, and Diagnostic Suppression Design

## Goal

Ship one OTA release on top of 0.1.17 that makes large screens easier to manage, exposes truthful update progress, fixes report visibility for every system role, replaces administration park controls with a reusable multi-select, suppresses explicitly ignored diagnostic errors, and removes permanent wheel hotspots while retaining real error markers.

## Constraints

- Preserve the current single-park context outside administration.
- Avoid a database migration: diagnostic unknowns already support `new`, `mapped`, and `ignored` states.
- Do not derive update progress from elapsed time. Progress must follow durable host phases.
- Do not hide real diagnostic markers created by active diagnostic rules.
- Keep the OTA compatible with an installed 0.1.17 system and sign it with the active release key.

## Collapsible Large Panels

Both panel implementations gain an explicit opt-in collapsible mode with a stable storage key. A collapsible panel starts open unless a saved browser preference says otherwise. Its header contains an accessible expand/collapse button, and collapsed content is removed from the accessibility tree.

Only massive or potentially unbounded panels opt in: administration configuration groups, diagnostics lists/editors, long work/report areas, and other screens whose contents can grow substantially. Small status, identity, metric, and warning cards remain permanently visible. The implementation will not infer collapsibility from rendered height, because that would make behavior depend on viewport and data timing.

## OTA Progress

The host already publishes a durable, allow-listed update phase. While an update job is active, the API projects the matching host job's phase into its public job response. The web client maps phases into ordered progress milestones covering archive validation, unpacking, image build, smoke test, snapshot, publication, migration, startup, and health check.

The UI renders a native determinate progress bar, a percentage, and the current localized phase. Percentages advance only when the host reports a new phase. Rollback phases are shown explicitly rather than pretending that forward progress continues. Unknown phases use an indeterminate-safe presentation and never expose raw host payloads.

## Report Routing and Visibility

Report access remains permission-gated, but system roles have explicit routing rules:

- `royal`: sees every report across every active park plus parkless system reports, regardless of target role.
- `admin`: sees administrative and parkless system reports; existing author access remains.
- `operator`: sees operator-targeted reports only for assigned parks.
- `mechanic` and `driver`: see reports they authored, including returned reports, within their permitted parks.

Automatic stale-cookie reports remain targeted to administration and must be visible in royal's inbox and badge even if royal was not their author. API tests will cover inbox, badge, detail access, action authorization, park boundaries, and the stale-cookie automatic report for all five system roles. Frontend tests will verify that royal receives and renders the badge and inbox.

## Administration Park Multi-select

A reusable accessible park multi-select replaces every administration form control that assigns or filters multiple parks. It is a dropdown with checkboxes, a text filter, selected-count summary, and `Select all`/`Clear` actions. Keyboard focus, Escape dismissal, and outside-click dismissal follow the existing shell selector conventions.

The control stores an array of numeric park IDs and preserves each existing API contract at its boundary. It does not change the global `ParkScopeProvider` or work-page URLs. Empty-selection validation remains owned by the consuming administration form so screens that legitimately allow no parks continue to do so.

## Ignored Diagnostic Errors

The existing ignore action changes a `DiagnosticUnknown` row to `ignored`. Live diagnostic projection will load ignored unknown identities and remove matching raw events before returning a robot snapshot. Because capture receives the filtered event list, ignored errors also stop accumulating sightings. Active mapped rules are unaffected.

Reopening an ignored item makes it visible on the next uncached robot read. Ignore/reopen invalidates the short-lived robot payload/snapshot cache so the UI reflects the decision promptly. The administration copy changes from "deferred" to "ignored" and states clearly that ignored errors are hidden until restored.

## Robot Diagram

All permanent wheel hotspot buttons and their selection state/details are removed from the robot image. Existing diagnostic event markers remain and continue to illuminate the configured location only when a matching error is present. Textual wheel fault information may remain in the diagnostic details when it is part of returned telemetry, but there are no always-visible wheel circles.

## Error Handling and Compatibility

- If live host progress cannot be read, polling continues and the last safe job phase remains visible.
- Report authorization fails closed for unknown/custom roles.
- Ignored identities are compared using the same deterministic event identity already stored by the unknown-diagnostic inbox.
- Existing saved panel state is namespaced and optional; corrupt values fall back to open.
- No raw logs, cookies, host paths, or upstream diagnostic payloads are added to public responses.

## Verification

- API unit/integration tests for role routing, badge counts, cookie alert visibility, ignored-event suppression, reopening, and cache invalidation.
- Host/API tests for live phase projection and phase allow-listing.
- Web component tests for collapsible panels, park multi-select accessibility, progress milestones/rollback, royal report badge/inbox, and absence of wheel hotspots.
- Full API and web test suites, production web build, release archive inspection, signature verification, and SHA-256 calculation before delivery.
