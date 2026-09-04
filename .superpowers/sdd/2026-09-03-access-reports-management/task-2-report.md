# Task 2 report — Management routes, Reports and More

## Shipped

- Added capability-aware Management routing: `/admin` is a hub, while `/admin/users`, `/admin/roles`, and `/admin/settings` have independent gates.
- Kept user management independent of the legacy integration bootstrap. A `users.manage`-only account can load the Users screen even when integrations fail.
- Moved legacy Admin user/role tabs out to their own routes and made legacy settings tabs URL-addressable (`?tab=integrations|parks|ops`).
- Allowed voluntary password changes for approved users while preserving the forced-change, pending, rejected, and current-password gates.
- Made Reports creator and resolver capabilities independent; a dual-capability user sees author controls and an Inbox tab.
- Scoped report list/detail cache keys and report-badge cache keys to principal, effective permissions, and selected park where applicable.
- Added report attachment client support, file retry on the already-created report (no second create POST), and authorized detail download links.
- Added a profile/change-password entry to More while retaining theme and density controls.

## TDD evidence

Red tests were observed before their corresponding implementation for:

- granular Management hub access and voluntary password change;
- dual report creator/resolver UI;
- report attachment transport and authorized detail link;
- attachment retry without duplicating report creation;
- More → change-password link.

## Verification

- Focused Task 2 tests: 608 passed.
- Full web test suite: 76 files, 1162 tests passed.
- TypeScript project build: `tsc -b` passed.
- Navigation inventory: `scripts/check-nav.mjs` passed (`25 route ids`).
- Lint completed with six pre-existing warnings outside Task 2 files and no errors.
- Playwright browser scenario was not completed: its config requires `npm run dev`, but the provided runtime has Node only (no npm); a direct local Vite server additionally needs an unsandboxed loopback listener. No visual baselines were regenerated.

## Limitations / follow-up

- The legacy settings page still retains its existing aggregate bootstrap for integrations, park settings, and ops. The independently routed Users/Roles pages do not rely on it.
- The existing `SlaPolicyEditor` was not mounted into the legacy parks tab in this task; it remains available in Operations pending a small selected-park integration that avoids duplicating park-selection state.
- No live API, `.env`, database, migration, deployment, push, or merge was run.

## Fix round 1/5

### RED / GREEN evidence

- RED: a dual-capability Reports session wrote mine/inbox resource keys into localStorage. GREEN: explicit nonpersistent report resources leave no `robopark:res:reports:` entries.
- RED: creator/resolver navigation rendered an Inbox heading but no semantic Inbox tab. GREEN: independent semantic tabs are rendered and switch the visible pane.
- RED: attachment retry had no client-side kind/size validation. GREEN: UI now validates the server-supported image/log families and limits before upload; retry remains bound to the created report ID.

### Changes

- Report mine, inbox and detail cache entries are nonpersistent; selection is scope-stamped so a principal/access/park change cannot render a previous detail during the effect window.
- My/Inbox panes render independently, choose their own list error/loading source, and My reports can select a read-only authorized detail. Added status filtering.
- Badge requests/key use the selected park for every park-scoped shell principal, plus principal and effective permissions.
- Parks-only settings start in parks mode and fetch only the park catalogue; integration, tracker, screenshot, requests and users bootstrap calls are not made. Park requests remain absent without `nav.admin`.
- Mounted `SlaPolicyEditor` in parks settings using the shared shell-selected park.
- Non-owner user/role editors omit privileged role and permission choices; attachment form now advertises and validates supported kinds/limits. `Report.park_id` is nullable.

### Verification

- Focused report/shell/management tests: 46 passed.
- Full web suite: 76 files, 1162 tests passed.
- TypeScript build and navigation check passed.
- Lint produced the same six pre-existing warnings outside Task 2 files; no errors.

## Fix round 2/5

### RED / GREEN evidence

- RED: `/admin/settings?tab=ops` as a `parks.manage` user normalized to `/admin/settings`, selecting the hidden integrations fallback. GREEN: it normalizes to `?tab=parks`, never calls `integrationSettings`, and does not render the secrets panel.
- RED: a resolver-only account initialized `mine`, producing no Reports pane and exposing the archive status selector. GREEN: it derives `inbox` synchronously from capabilities and Inbox is constrained to open reports with no status selector.
- RED: a mobile `application/octet-stream` HEIC upload never reached the attachment endpoint. GREEN: a blank/generic MIME supported filename is accepted, while explicit incompatible MIME remains rejected and the existing per-kind limits remain enforced.

### Changes

- Settings now accepts only allowed URL tabs, uses its first permitted tab as fallback, and conditionally mounts integrations only for the exact `nav.admin` capability. Parks-only remains API-isolated.
- A single owner-aware privileged permission catalog is used by both user and role editors. It excludes `nav.admin`, tracker/emergency admin navigation, parks/users/roles management and `users.approve` for non-owners; royal retains the full catalog.
- Reports derives the visible pane from live capabilities, scopes selection to the live principal/access/park stamp, constrains Inbox to `open`, and resolves detail park labels from each report's `park_id` (including platform/unknown fallbacks), never the shell's selected park.
- The attachment input advertises supported extensions, and accepts blank/octet-stream mobile image/log uploads only when the filename extension is allowed.

### Verification

- Focused settings/reports/permissions tests: 32 passed.
- Full web suite: 77 files, 1167 tests passed.
- TypeScript project build and `scripts/check-nav.mjs` passed (`25 route ids`).
- Targeted lint passed with no warnings after correcting the hook dependency.

### Superseded note

- The round-1 legacy-settings and SLA limitations above are superseded: parks settings now normalizes safely and mounts `SlaPolicyEditor` with the shared selected-park scope.

## Fix round 3/5

### RED / GREEN evidence

- RED: `image/jpg` and `application/octet-stream` photo bytes with no recognized filename extension were rejected before `reportAttach`; the regression tests observed zero upload calls.
- GREEN: both forms reach `reportAttach` for the already-created report and assert exactly one `createReport` call. Explicit incompatible MIME remains outside the accepted image set; the 15 MiB photo limit is unchanged and generic bytes remain server-validated by signature.

### Verification

- Focused `ReportForms` tests: 4 passed.
- TypeScript build, targeted lint, and `git diff --check` passed.

## Fix round 4/5

### RED / GREEN evidence

- RED: a `binary/octet-stream` image with no recognized filename extension was filtered before `reportAttach`; the focused regression observed zero upload calls while the other four `ReportForms` cases passed.
- GREEN: the generic-image MIME path and file-input declaration now include the server-supported `binary/octet-stream` value. The regression reaches `reportAttach(42, 'device_photo', file)` on the already-created report and asserts exactly one `createReport` call.
- Explicit incompatible MIME rejection remains in place, as do the existing 15 MiB image and 64 KiB log limits; generic image bytes remain subject to server-side signature validation.

### Verification

- Focused `ReportForms` tests: 5 passed.
- TypeScript project build, targeted lint, and `git diff --check` passed.
