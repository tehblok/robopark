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
