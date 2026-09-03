# Access, Reports and Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore discoverable account/role management, prevent privilege escalation and make report creation available to every approved role.

**Architecture:** Keep existing protected user/role/report operations, repairing permission checks and separating management modules from integration bootstrap. Expose independent management routes with capability gates; shared shell retains navigation ownership. Reports provide author and handler workflows independently.

**Tech Stack:** React/TypeScript, FastAPI/SQLAlchemy, existing report attachments and RBAC tables.

**Spec:** docs/superpowers/specs/2026-09-03-operations-corrections-design.md (Доступы, репорты и управление).

## Global Constraints

- Work in `/Users/tehblokdan/Desktop/Проекты/robopark/.worktrees/robopark-redesign`; no secrets/`.env`/live API/database, no dependency installation, main merge or push.
- Reuse `/Users/tehblokdan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node`; API `.venv/bin/python` with `Settings.model_config["env_file"] = None` before pytest import.
- Owner can manage all accounts; non-owner managers cannot grant new privileged effective permissions via create, role change or explicit permissions. Existing last-owner/self-delete guards stay.
- Explicit per-user and per-role revocations survive restart. Default permissions belong to initial seeding, not unconditional re-grant on each startup.
- Every approved standard role has default report creation/navigation; an explicit owner revoke remains effective. Report processing/viewing others remains separate permission and scope.
- No plaintext passwords persisted in UI/localStorage/logs; clear draft secrets after success/selection/account changes. Buttons prevent duplicate mutations and show failure without false success.
- Responsive 44px mobile controls, existing light/dark/system themes, keyboard/focus/error handling, scope-keyed nonpersistent protected caches.

### Task 1: Permission integrity and report scope

**Files:**
- Modify `apps/api/src/robopark_api/routers/admin_users.py`, `routers/admin_roles.py`, `services/rbac.py`, `services/rbac_seed.py`, `routers/reports.py`, `services/reports.py`, relevant tests.
- Create `apps/api/alembic/versions/0017_driver_work_reports.py` (after insights plan's 0016) for existing driver default task/read/report capabilities. Preserve UserPermission denies. New installations seed updated defaults. Migration does not grant writes/attachments.
- Add `apps/api/tests/test_management_permissions.py` or focused tests in existing role/user/report suites.

**Interfaces:** Existing public API payloads remain compatible. No role_slug or permissions combination may bypass privileged-grant checking. Creation compares proposed effective grants against empty prior permissions; update compares against the target's actual effective permissions before any mutation.

- [ ] Add RED tests for a users.manage-only actor creating admin without explicit permissions, changing mechanic to admin, changing to custom privileged role, and restoring a revoked privileged permission. All denied, no persisted partial mutation. Owner cases succeed. Nonprivileged role creation/change stays supported. Last active owner cannot be disabled/demoted/deleted.

```python
response = client.post('/admin/users', json={
    'username': 'unauthorized-admin', 'password': 'Strong-pass-42', 'role_slug': 'admin'
})
assert response.status_code == 403
assert db_session.scalar(select(User).where(User.username == 'unauthorized-admin')) is None
```

- [ ] Add RED tests: revoked system-role permission remains revoked after `ensure_rbac_catalog`; driver's explicit per-user report deny survives upgrade; standard driver gains task/read/report defaults but no writes. Creation works for approved assigned mechanic/driver/operator and admin/royal in authorized fleet park; denied/unapproved/foreign-park cases fail.
- [ ] Implement validation before applying user changes. Compute proposed permissions from requested role or explicit payload without treating the new role as pre-existing authority. Protect existing owner invariants. Default seeding must not overwrite explicit system role changes; migration upgrades only intended driver capability delta and is idempotent.
- [ ] Users.manage-only accounts need read access to role choices and permission catalogue to edit users correctly. Permit these read-only catalogue/list operations for users.manage or roles.manage; role mutations still require roles.manage. Test that this does not grant role editing or privileged assignments.
- [ ] Unify report park authorization for creation/read/actions. Admin/royal have fleet scope; others assigned active parks. Keep target-role routing (operator inbox, admin escalation) and make permissions gate processing consistently. A creator can read own report independent of report processing permission. Do not allow a driver to read arbitrary others' reports merely because creation is now enabled.
- [ ] Run focused role/user/report tests GREEN and Ruff; test migration on isolated temporary DB only. Commit exact files and report TDD/results.

### Task 2: Management routes, account tools, Reports and More

**Files:**
- Create `apps/web/src/domains/management/ManagementPage.tsx`, `UserManagementPage.tsx`, `RoleManagementPage.tsx`, `management.css`, tests.
- Modify `apps/web/src/pages/Admin.tsx`, `components/admin/AdminUsersPanel.tsx`, `AdminRolesPanel.tsx` as needed for independent loading and guards.
- Modify `apps/web/src/pages/Reports.tsx`, report components and adjacent tests; optionally split scoped resource owner/list/detail into `domains/reports` rather than growing a monolith.
- Modify `apps/web/src/app/routing/routeManifest.ts`, `accessPolicy.ts`, `AppRouter.tsx`, routing tests and `scripts/check-nav.mjs` expected inventory if needed.
- Modify `apps/web/src/app/shell/AppShell.tsx`, `AppShell.css`, tests for grouped More/profile link, preserving workspace corrections.

**Interfaces:**
- `/admin` becomes Management hub; legacy integration/parks/operations UI moves to `/admin/settings`, reusing existing Admin component with URL tab (`integrations`, `parks`, `ops`) and loading only what that module needs.
- `/admin/users` guarded by users.manage, `/admin/roles` by roles.manage, each independent from nav.admin/integration bootstrap. Hub is reachable for any of nav.admin/users.manage/roles.manage/parks.manage; each destination still enforces own capability and approved account. `/admin/tracker` and `/admin/emergency/config` remain existing routes.
- A single Management nav item owns these sections unless a more-specific permitted item is deliberately shown. User with a granted management capability can find the module without unrelated integration privileges. Main admin/royal default landing remains useful.
- Mount insights `SlaPolicyEditor` for permitted selected park inside Management/park settings, no duplicated calculation or speculative defaults.
- Reports creator=`reports.create`, inbox=`reports.resolve`, independent booleans. Both may be true. My reports, inbox, detail and creation have clear navigation and per-resource errors.

- [ ] RED UI tests: owner sees Users/Roles/Parks and opens them; mocked integration failure does not block Users; granular users.manage-only account reaches Users; role/password/park/section access editing emits correct payload; illegal role options absent for non-owner; delete requires explicit confirmation and handles server protection.
- [ ] RED report tests across all roles, explicit permission denial, dual creator+resolver, selecting own report detail, preserving form text on submission error, no duplicate post, attachments/retry and park/user switching hides prior protected data. Author pane remains usable if inbox fails. Report status filters and readable task/park context.
- [ ] RED More tests: grouped secondary routes, active current section after navigation, profile/change-password accessible; theme/density retained. Unknown/capability-denied routes do not become selectable.

```tsx
// An operator may create a report AND handle incoming reports.
expect(screen.getByRole('button', { name: 'Создать репорт' })).toBeVisible()
expect(screen.getByRole('tab', { name: 'Входящие' })).toBeVisible()
```

- [ ] Implement using existing design system and backend operations. Separate Users/Roles resource owners from legacy Admin integration bootstrap, account/permission keyed and unmount-safe. Keep reset password/park/permissions/delete functionality, improve grouping/filter/detail actions rather than dropping operations. Display unsupported/permission errors honestly.
- [ ] Reports: role-neutral creation labels (not mechanic-only), My/Inbox tabs, status selection, detail and author/handler actions; no special mechanic permission bypass. Refresh scoped lists and badge after successful changes, not after errors. Scope all caches to principal+effective access+park and invalidate released owners to block late writes.
- [ ] Run focused web tests, TypeScript, navigation checks. Commit owned files, report results and any remaining unsupported action.

## Acceptance

- [ ] One combined final review + whole web/API verification after workspace/insights/access integration. Check all five roles on desktop and phone; do not regenerate Linux screenshot baselines from macOS.
- [ ] Record shipped changes, exact migration requirements and limitations. Do not claim unexecuted deployment or live-data verification.
