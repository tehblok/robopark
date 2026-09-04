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
