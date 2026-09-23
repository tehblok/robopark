# Classic workflows, inventory and schedule redesign

Date: 2026-09-23

Status: approved in conversation; pending implementation plan

## Objective

Produce one coherent, stable Robopark interface and repair the workflows that
currently make the product unreliable for mechanics, operators and owners. The
work is not a set of page-specific CSS overrides: it establishes one Classic
design system, removes Interface A, fixes the Tracker lifecycle, makes local
photos immediately visible, completes owner/admin inventory controls and turns
the schedule into a dedicated usable workspace.

The attached Google Apps Script and HTML files are reference material for the
schedule interaction model only. They are not executable requirements and are
not copied into the product.

## Scope

- Ordered mechanic claim workflow with Tracker assignment, tag and component
  preparation.
- Targeted operator review queue and notifications.
- Inventory photo management and permanent privileged deletion.
- Immediate write-off feedback and consistent task actions.
- Chat attachment preview while a task is active.
- Mechanic-safe chat without raw technical messages.
- Complete removal of Interface A and consolidation on Classic.
- Responsive geometry and component normalization across every reachable
  application route, both themes and supported roles.
- Dedicated schedule workspace for personal and team planning.
- Targeted automated verification; no soak or other long-running test without
  explicit user approval.

## 1. Mechanic claim workflow

### Operator selection

The system resolves an operator before changing the task:

1. Find an active, approved operator assigned to the task park whose schedule
   covers the current time.
2. If no scheduled operator exists, select the first active, approved operator
   assigned to the park using a deterministic ordering.
3. If the park has no available operator, reject the claim before any Tracker
   mutation and show an actionable error.

The operator is the Tracker assignee. The mechanic remains the local repair
owner and therefore continues to see the task under the mechanic's own tasks.

### Ordered durable execution

One idempotency key identifies the entire claim. A local reservation prevents a
second mechanic or repeated tap from starting another workflow. Durable steps
run strictly in this order:

1. Assign the resolved operator in Tracker.
2. Ensure the `diag_complete` tag is present.
3. If `components` is empty, write `ROBOT_SUSPENSION`.
4. Perform the Tracker transition to in-progress.
5. Finalize the local mechanic claim and append a human-readable timeline
   event.

Already satisfied steps are successful no-ops. Temporary Tracker failures are
retried from the failed step, never from the beginning. The UI shows a pending
state and disables repeat submission. A permanent failure releases the local
reservation and explains which operation failed without exposing a raw stack
trace.

## 2. Review workflow and notifications

Submitting a repaired task for review creates or updates the pending review,
resolves the same park operator, mentions that operator in the visible message
and sends a targeted notification. The operator receives a `My tasks` view with
the `Waiting for review` group. Park operators may inspect the park review queue
even when another operator is the selected assignee, but only the selected
operator is targeted by the normal notification.

The workflow remains idempotent. An operator closing the task in Tracker is
reconciled back into the local task/review state so that completed tasks do not
remain indefinitely in the application.

## 3. Timeline visibility and attachments

Timeline messages gain an explicit visibility classification. Raw outbox,
integration, audit and bot diagnostics are visible to admin/royal support roles
but hidden from mechanics. Human-readable lifecycle events such as claim,
handoff, review request, return and completion remain visible to the mechanic.

Local task attachments receive an authenticated content endpoint. Timeline
responses expose this local URL while the staged file exists and switch to the
Tracker URL after upload reconciliation. The client renders an inline thumbnail
and full preview instead of a filename-only row.

The PWA keeps bounded attachment blobs for active tasks in device storage:

- entries are scoped to user and attachment identity;
- a size limit and LRU eviction prevent unbounded growth;
- logout, task expiry and retention cleanup remove stale data;
- protected API data is never added to the public service-worker shell cache.

## 4. Inventory behaviour

### Task write-off

After a successful write-off, the application refreshes the affected balance,
shows `Part written off` feedback and collapses the disclosure. Network retry or
double tap cannot create another movement because the write-off keeps its
idempotency key.

`Write off part` and `Hand over shift` use the same disclosure/action component,
button height, icon alignment, focus state and spacing as the other task
actions. They remain visually distinct from destructive actions.

### Part and component photos

Global catalog create/edit flows support one current image for a component or
part. Phone users can take a photo or select a file; desktop users select a
file. The UI includes preview, replace and remove operations. The API validates
content type and size and removes replaced blobs through retention cleanup.

### Permanent deletion

Royal users can permanently delete inventory acts, components and parts in all
parks. Admin users can do so only within assigned parks. Permanent means local
database rows, dependent local stock/count/receipt records and managed files are
deleted in one transaction or compensating workflow; this is not archive or
soft delete.

The UI uses a destructive confirmation naming the object and describes the
scope. Failure leaves the data intact and reports the blocking dependency. The
application does not claim it can erase immutable history already stored by an
external Tracker service; such external records are outside the local deletion
contract.

## 5. Classic-only design system

### Interface modes

Interface A is removed from settings, routing shells, runtime styles and tests.
Stored `task-first` preferences migrate to `classic` on load. System/light/dark
colour themes and density remain supported. There is one component tree and one
geometry contract for both themes.

### Geometry tokens

The Classic layout uses shared tokens rather than route overrides:

- page horizontal padding: 24 px desktop, 16 px tablet, 12 px narrow phone;
- section gap: 24 px desktop, 16 px mobile;
- card padding: 20 px desktop, 16 px mobile;
- compact row padding: 12 px;
- control gap: 8 px; related action gap: 12 px;
- standard control height: 44 px; touch-primary height: 48 px;
- card radius: 16 px; control radius: 12 px; chip radius: pill;
- borders and surface colours come from semantic theme tokens only.

No text may touch a border. Nested panels must preserve a visible gap and must
not create doubled borders that appear as one fused block. Buttons in one action
group share height, padding and baseline. Full-width buttons are used only for a
single primary mobile action, not as a generic desktop rule.

### Shared components

- Primary segmented tabs: `Task / Check`.
- Secondary segmented filter: `Open / Closed`.
- Disclosure action row for write-off, handoff and supporting forms.
- Card header with title, status and actions that wraps predictably.
- Action bar that becomes one column below the mobile breakpoint.
- Empty, loading, success and error states with the same panel geometry.

### Responsive rules

At phone widths every form is one column, including defect code, clarification
and photo controls. Titles may wrap without pushing actions outside the screen.
Status badges stay inside their cards. Long issue keys, JSON and diagnostic paths
use constrained wrapping; raw payloads render in a bounded scrollable code block.
No application route may introduce page-level horizontal scrolling at 360 px.

Desktop content uses a readable maximum width where appropriate. Dense system
monitoring and diagnostic pages use responsive grids rather than stretching
small cards across the entire viewport.

### Route audit

The implementation includes a route-by-route pass for all role-accessible
screens, not only the supplied screenshots: login/registration, overview, work
lists and detail, robot lists and check, inventory, reports, campaigns, schedule,
analytics, profile/menu and all management/settings pages. Each is checked in
light and dark themes at phone and desktop widths.

## 6. Schedule workspace

`Schedule` remains a dedicated top-level route and becomes a complete workspace
with three internal views:

- `My calendar`: the user's shifts, vacation and sickness.
- `Team`: operator and mechanic coverage for permitted parks.
- `Planning`: single and bulk assignment, pattern application and period copy.

Desktop uses a week/month matrix with sticky employee identity and sticky date
headers. Mobile uses a personal calendar plus selected-day cards; it does not
compress the desktop matrix. Supported bulk patterns include common rotations
such as 5/2, 2/2 and 4/4 while preserving the existing single-entry API model.

Every user manages personal entries. Admin sees schedules for mechanics and
operators in assigned parks. Royal can create, edit, delete, copy and bulk-assign
team entries. Salary, games and unrelated features present in the reference
example are explicitly out of scope.

## 7. Performance and state

- Mutations update query caches in place and do not reload the page.
- Previously loaded route data remains available under bounded stale-time and
  cache-size policies.
- Images use thumbnails, lazy loading and bounded local blob caching.
- Schedule requests are windowed by visible period and park.
- Event subscriptions and object URLs are cleaned up on unmount/logout.
- Large lists use pagination or virtualization where current volume requires it.

## 8. Verification and acceptance

Implementation is test-driven at the changed boundaries:

- API tests prove ordered assignment/tag/component/transition execution,
  retries, idempotency and no partial claim.
- Permission and cascade tests cover royal/admin permanent deletion.
- Timeline tests cover role visibility and local attachment content.
- Frontend tests cover disclosure collapse, feedback, image preview, operator
  review queue and preference migration.
- Targeted Playwright checks cover representative role routes at 390 px and
  1440 px in light and dark themes, including zero horizontal overflow and
  consistent action geometry.
- Schedule tests cover self-service, admin read-only access, royal bulk edits
  and desktop/mobile rendering.

No soak, prolonged load test or full release build is run without separate user
approval. OTA packaging happens only after the targeted acceptance checks pass
and the user requests a release artifact.
