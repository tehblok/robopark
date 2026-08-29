# Global-review fixes — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every remaining P1/P2/P3 finding from the 2026-08-29 global review (abort, Docker bake, 401/429 copy, CSP, logout, parks, RBAC, screenshot guard, cache, stubs, docs).

**Architecture:** Small, isolated fixes. Ops abort becomes cooperative via a locked `save_job` that refuses to resurrect a failed job. Privileged RBAC grants are checked in one helper used by roles and users. Frontend park state lives in `ParkProvider`. Error mapping is detail-first, status-second.

**Tech Stack:** FastAPI, SQLAlchemy, pytest; React 19, Vite, vitest; nginx; Docker.

## Global Constraints

- Russian UI copy only. Do not restore a hint under the login «Запомнить меня» checkbox.
- Do not commit `apps/api/data/ops/`, secrets, `.env`, or snapshot ZIPs.
- Keep auth cookies HttpOnly + SameSite=lax.
- Attach-without-`tracker.write` stays by design (`ensure_action_allowed` attach branch).
- Do not expand driver VIN ACL.
- Work in the current workspace on `feat/royal-ops-and-security-hardening`. Do not create a git worktree.
- Uncommitted remember-me work already exists (idle 3d / absolute 30d, login checkbox). If you edit those files, keep that behavior. If you do not edit them, do not `git add` them.
- Commit only the files this task lists. Never `git add -A`. Do not push.
- API tests: `cd apps/api && .venv/bin/python -m pytest <files> -q` (needs the project venv). Web tests: `cd apps/web && npm test`.

## File map

- `apps/api/src/robopark_api/services/ops/jobs.py` — abort-safe `save_job`
- `apps/api/src/robopark_api/services/ops/runner.py` — catch abort, stop mutating
- `apps/api/.dockerignore`, `apps/api/Dockerfile` — do not bake `data/ops`
- `apps/web/nginx.conf` — CSP on `/index.html`
- `apps/web/src/i18n/errors.ts`, `apps/web/src/i18n/ru.ts`, `apps/web/src/pages/Login.tsx` — 401/429
- Standalone auth pages + `ChangePassword.tsx` — logout → `/login`
- `apps/web/src/auth.tsx`, `apps/web/src/lib/recentRobots.ts` — clear cache on 401/logout
- `apps/web/src/App.tsx`, operator park pages, `ParkProvider` — one park context
- `apps/api/src/robopark_api/services/rbac.py`, `admin_roles.py`, `admin_users.py`, `tracker_policy.py`
- `ScreenshotGuard.tsx`, `screenshotGuardLogic.ts`, `nav-permissions.ts`, `Home.tsx`, `Register.tsx`, READMEs

---

### Task 1: Cooperative ops abort

**Files:**
- Modify: `apps/api/src/robopark_api/services/ops/jobs.py`
- Modify: `apps/api/src/robopark_api/services/ops/runner.py`
- Test: `apps/api/tests/test_ops_jobs_abort.py` (create) and extend `apps/api/tests/test_ops_runner.py` if a runner hook is the cleanest place

**Interfaces:**
- Produces: `class JobAborted(RuntimeError)`
- Produces: `save_job(ops_dir: Path, job: OpsJob) -> None` raises `JobAborted` instead of overwriting a same-id `failed` job with `queued`/`running`/`succeeded`
- Produces: `fail_job` / `succeed_job` / `append_log` / `execute_job` must not resurrect an aborted job

- [ ] **Step 1: Write the failing tests**

Create `apps/api/tests/test_ops_jobs_abort.py`:

```python
from pathlib import Path

import pytest

from robopark_api.services.ops.jobs import (
    STATE_FAILED,
    STATE_RUNNING,
    STATE_SUCCEEDED,
    JobAborted,
    abort_job,
    load_job,
    new_job,
    save_job,
)


def test_save_job_does_not_resurrect_aborted(tmp_path: Path):
    ops = tmp_path / "ops"
    job = new_job("restore", exempt_token_hash="x")
    job.state = STATE_RUNNING
    save_job(ops, job)
    aborted = abort_job(ops)
    assert aborted is not None
    assert aborted.state == STATE_FAILED

    job.phase = "unpacking"
    with pytest.raises(JobAborted):
        save_job(ops, job)

    disk = load_job(ops)
    assert disk is not None
    assert disk.state == STATE_FAILED
    assert disk.error == "aborted"
    assert disk.phase == "failed"


def test_save_job_allows_terminal_failed_write_after_abort(tmp_path: Path):
    """fail_job after abort must not crash; disk stays failed."""
    ops = tmp_path / "ops"
    job = new_job("update", exempt_token_hash="x")
    job.state = STATE_RUNNING
    save_job(ops, job)
    abort_job(ops)
    job.state = STATE_FAILED
    job.error = "aborted"
    job.phase = "failed"
    save_job(ops, job)
    disk = load_job(ops)
    assert disk is not None
    assert disk.state == STATE_FAILED
```

Add in `apps/api/tests/test_ops_runner.py` a test that abort during `test_runner` leaves the job failed with `aborted` (not `succeeded` / not `tests_failed` overwriting abort). Pattern: `test_runner` calls `abort_job(ctx.ops_dir)` then raises or returns; `start_and_run` must end with `error == "aborted"` and `state == failed`. If the current `run_update` path always maps `ReleaseTestsFailed` to `tests_failed`, change the runner so `JobAborted` wins.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_ops_jobs_abort.py tests/test_ops_runner.py -q
```

Expected: FAIL — `JobAborted` is not defined, or abort is overwritten.

- [ ] **Step 3: Implement abort-safe save and runner catch**

In `jobs.py`:

- Add `class JobAborted(RuntimeError): ...`
- `save_job` must take the same exclusive flock as `begin_exclusive` (`ops_paths()["lock"]`) around load+write.
- After acquiring the lock, `disk = load_job(ops_dir)` (read the file; do not deadlock — `load_job` must not take the lock).
- If `disk is not None` and `disk.id == job.id` and `disk.state == STATE_FAILED` and `job.state in {STATE_QUEUED, STATE_RUNNING, STATE_SUCCEEDED}`: raise `JobAborted(disk.error or "aborted")`.
- Otherwise write atomically as today.
- `abort_job` stays as-is (it writes `STATE_FAILED`, which is allowed).

In `runner.py`:

- Import `JobAborted`.
- `append_log` already calls `save_job`. Catch `JobAborted` in `fail_job`, `succeed_job`, `execute_job` / `run_*`: reload `load_job(ctx.ops_dir)` and return that job (do not replace `error`).
- Before destructive steps in `run_restore` / `run_update` (unpack, db replace, file copy), call `save_job` or a tiny `raise_if_aborted(ctx.ops_dir, job)` that loads disk and raises `JobAborted` if failed. Reuse the `save_job` guard: e.g. `save_job(ctx.ops_dir, job)` while still `STATE_RUNNING` will raise if aborted.

Do not start a second job. Do not change HTTP abort payload.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_ops_jobs_abort.py tests/test_ops_runner.py tests/test_security_hardening.py tests/test_ops_http.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/robopark_api/services/ops/jobs.py \
  apps/api/src/robopark_api/services/ops/runner.py \
  apps/api/tests/test_ops_jobs_abort.py \
  apps/api/tests/test_ops_runner.py
git commit -m "fix(ops): honor abort so runners cannot resurrect a failed job"
```

---

### Task 2: Docker bake + nginx CSP on index.html

**Files:**
- Modify: `apps/api/.dockerignore`
- Modify: `apps/api/Dockerfile`
- Modify: `apps/web/nginx.conf`

**Interfaces:**
- Image must contain `data/emergency_sections.json` and must not copy `data/ops/`.
- `location = /index.html` must send the same CSP + Permissions-Policy as `location /assets/`.

- [ ] **Step 1: Write a small regression test for dockerignore**

Create `apps/api/tests/test_dockerfile_data.py`:

```python
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]


def test_dockerignore_excludes_ops():
    text = (API_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "data/ops" in text


def test_dockerfile_does_not_copy_whole_data_tree():
    text = (API_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY data ./data" not in text
    assert "emergency_sections.json" in text
```

- [ ] **Step 2: Run it — expect FAIL**

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_dockerfile_data.py -q
```

- [ ] **Step 3: Implement**

`apps/api/.dockerignore` — append:

```
data/ops
*.db
```

`apps/api/Dockerfile` — replace `COPY data ./data` with:

```
COPY data/emergency_sections.json ./data/emergency_sections.json
```

`apps/web/nginx.conf` — in `location = /index.html`, after the existing Cache-Control / nosniff / frame / referrer headers, add the same two lines used under `/assets/`:

```
add_header Permissions-Policy "geolocation=(), microphone=(), camera=()" always;
add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://*.tile.openstreetmap.org https://tile.openstreetmap.org; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" always;
```

Do not drop Cache-Control on index.html.

- [ ] **Step 4: Re-run dockerignore tests**

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_dockerfile_data.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/.dockerignore apps/api/Dockerfile apps/api/tests/test_dockerfile_data.py apps/web/nginx.conf
git commit -m "fix(ops): stop baking ops data and restore CSP on index.html"
```

---

### Task 3: mapApiError 401 + login 429

**Files:**
- Modify: `apps/web/src/i18n/errors.ts`
- Modify: `apps/web/src/i18n/ru.ts`
- Modify: `apps/web/src/pages/Login.tsx`
- Test: `apps/web/src/i18n/errors.test.ts` (create)

**Interfaces:**
- `mapApiError` uses `ru.errors.details[detail]` first (already).
- Status 401 maps to `ru.errors.emergency401` **only** when `detail === 'emergency_cookie_invalid'`.
- Any other 401 maps to new `ru.errors.sessionExpired`.
- Login catch uses `mapApiError(caught, ru.errors.login)` so 429 / `too_many_attempts` is not the generic login string.
- Add `ru.errors.details.too_many_attempts`.

- [ ] **Step 1: Failing vitest**

```ts
import { describe, expect, it } from 'vitest'
import { ApiError } from '../api'
import { mapApiError } from './errors'
import { ru } from './ru'

describe('mapApiError', () => {
  it('uses Emergency copy only for emergency_cookie_invalid', () => {
    const emergency = new ApiError(401, 'emergency_cookie_invalid')
    expect(mapApiError(emergency, 'fb')).toBe(ru.errors.details.emergency_cookie_invalid)
    const session = new ApiError(401, 'session_expired')
    expect(mapApiError(session, 'fb')).toBe(ru.errors.sessionExpired)
    const bare = new ApiError(401, '')
    expect(mapApiError(bare, 'fb')).toBe(ru.errors.sessionExpired)
  })

  it('maps too_many_attempts', () => {
    const err = new ApiError(429, 'too_many_attempts')
    expect(mapApiError(err, ru.errors.login)).toBe(ru.errors.details.too_many_attempts)
  })
})
```

If `ApiError` constructor differs, read `apps/web/src/api.ts` and match it.

- [ ] **Step 2: Run `cd apps/web && npx vitest run src/i18n/errors.test.ts` — expect FAIL**

- [ ] **Step 3: Implement**

`ru.ts`:

- `sessionExpired: 'Сессия истекла. Войдите снова.'` next to other `errors.*` strings.
- `too_many_attempts: 'Слишком много попыток. Подождите минуту.'` inside `errors.details`.

`errors.ts` — replace the blanket `if (error.status === 401) return ru.errors.emergency401` with:

```ts
if (error.status === 401) {
  if (error.detail === 'emergency_cookie_invalid') return ru.errors.emergency401
  return ru.errors.sessionExpired
}
```

Keep the existing `details[error.detail]` lookup **above** this so `emergency_cookie_invalid` still prefers `details`.

`Login.tsx` — `import { ApiError } from '../api'` is optional; `import { mapApiError } from '../i18n/errors'`, then:

```ts
} catch (caught) {
  setError(mapApiError(caught, ru.errors.login))
}
```

Do not add a caption under «Запомнить меня».

- [ ] **Step 4: Re-run vitest for the new file — PASS**

- [ ] **Step 5: Commit** (include `Login.tsx` / `ru.ts` even if they also contain remember-me hunks)

```bash
git add apps/web/src/i18n/errors.ts apps/web/src/i18n/errors.test.ts apps/web/src/i18n/ru.ts apps/web/src/pages/Login.tsx
git commit -m "fix(web): map session 401 and login throttle instead of Emergency copy"
```

---

### Task 4: Logout redirects, ChangePassword logout, cache + recent robots

**Files:**
- Modify: `apps/web/src/pages/OperatorPending.tsx`
- Modify: `apps/web/src/pages/OperatorRejected.tsx`
- Modify: `apps/web/src/pages/MechanicNoPark.tsx`
- Modify: `apps/web/src/pages/ChangePassword.tsx`
- Modify: `apps/web/src/lib/recentRobots.ts`
- Modify: `apps/web/src/lib/recentRobots.test.ts`
- Modify: `apps/web/src/auth.tsx`
- Note: `OperatorParks.tsx` logout is handled in Task 5 when it joins the shell. If you still see a standalone `onLogout` without Navigate after Task 5, Task 5 owns it. `NoCabinet.tsx` already navigates when `!user`.

**Interfaces:**
- After `logout()`, `user` is null → `<Navigate to="/login" replace />`.
- `clearRecentRobots()` removes `robopark.recentRobots` from localStorage.
- `resourceStore.clearAll()` + `clearRecentRobots()` on logout and on AuthProvider `api.me()` **401**.

- [ ] **Step 1: Failing test for clearRecentRobots**

In `recentRobots.test.ts`:

```ts
import { clearRecentRobots, loadRecentRobots, pushRecentRobot } from './recentRobots'

it('clearRecentRobots wipes storage', () => {
  pushRecentRobot('447')
  clearRecentRobots()
  expect(loadRecentRobots()).toEqual([])
  expect(window.localStorage.getItem('robopark.recentRobots')).toBeNull()
})
```

- [ ] **Step 2: Run vitest — FAIL (export missing)**

- [ ] **Step 3: Implement**

`recentRobots.ts`:

```ts
export function clearRecentRobots(): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.removeItem(STORAGE_KEY)
  } catch {
    /* ignore */
  }
}
```

Standalone pages (`OperatorPending`, `OperatorRejected`, `MechanicNoPark`):

```tsx
const { user, loading, logout } = useAuth()
if (loading) return null
if (!user) return <Navigate to="/login" replace />
```

Keep existing `onLogout={logout}` (or `() => void logout()`). Import `Navigate` from `react-router-dom`.

`ChangePassword.tsx`: destructure `logout` from `useAuth()` and pass `onLogout={logout}` to `PageShell`. It already navigates when `!user`.

`auth.tsx`:

```ts
import { ApiError } from './api'
import { clearRecentRobots } from './lib/recentRobots'

// in useEffect catch:
.catch((error) => {
  if (error instanceof ApiError && error.status === 401) {
    resourceStore.clearAll()
    clearRecentRobots()
  }
  setUser(null)
})

// login: after existing resourceStore.clearAll()
clearRecentRobots()

// logout: after resourceStore.clearAll()
clearRecentRobots()
```

- [ ] **Step 4: `cd apps/web && npx vitest run src/lib/recentRobots.test.ts` PASS**

- [ ] **Step 5: Commit** (auth.tsx / ChangePassword.tsx may include remember-me hunks — keep them)

```bash
git add apps/web/src/pages/OperatorPending.tsx \
  apps/web/src/pages/OperatorRejected.tsx \
  apps/web/src/pages/MechanicNoPark.tsx \
  apps/web/src/pages/ChangePassword.tsx \
  apps/web/src/lib/recentRobots.ts \
  apps/web/src/lib/recentRobots.test.ts \
  apps/web/src/auth.tsx
git commit -m "fix(web): send logged-out gates to login and drop session caches"
```

---

### Task 5: Operator park context + parks route in shell

**Files:**
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/pages/OperatorBlockers.tsx`
- Modify: `apps/web/src/pages/OperatorNowReport.tsx`
- Modify: `apps/web/src/pages/OperatorParks.tsx`
- Modify: `apps/web/src/components/parks/RequestParkModal.tsx` only if `onSubmitted` needs a refreshUser hook — prefer calling `refreshUser` from the pages

**Interfaces:**
- `/operator/parks` is a child of `AuthenticatedShellLayout` (same chrome as dashboard).
- `OperatorParks` drops `standalone` and `onLogout` (AppShell already logs out).
- `OperatorBlockers` uses `useParkContext().parkId` / `setParkId` / `parks` — no second park id. Remove the duplicate park `<select>` (AppShell already has one).
- `OperatorNowReport` reads `parks` and `parkId` from context. Local filter may keep «Все парки» (`null` → API without park id). Choosing a specific park must call `setParkId`.
- After a successful park request (`RequestParkModal` `onSubmitted`) and on `OperatorParks` after refresh, call `refreshUser()` so `ParkProvider` sees new `user.parks`.

- [ ] **Step 1: No dedicated unit test required for routing.** If `apps/web` has App route tests, extend them. Otherwise implement and run `cd apps/web && npm test`.

- [ ] **Step 2: Implement App.tsx**

Move the `/operator/parks` `<Route>` **inside** the `AuthenticatedShellLayout` tree (next to `/dashboard`), still wrapped in `RequireApprovedOperator`. Delete the sibling route outside the shell.

- [ ] **Step 3: Implement pages**

`OperatorBlockers.tsx`: `import { useParkContext } from '../park-context'` and `useAuth` for `refreshUser`. Remove local `parkId` state and the parks-from-cache-driven `useEffect` that defaulted `parkId`. Use context `parkId`. Keep `useCachedResource('operator:parks')` only if still needed for empty-state vs available parks; prefer context `parks` for the list. `onSubmitted`: `void refreshUser()` then refresh available-parks cache.

`OperatorNowReport.tsx`: `useParkContext()`. Keep a local `scopeAll` boolean default `false`. When `scopeAll`, call `api.operatorNowReport(undefined)`; otherwise `api.operatorNowReport(parkId ?? undefined)` and disable the report if `parkId == null` and parks exist. The park `<select>` «один парк» options call `setParkId` and set `scopeAll` false.

`OperatorParks.tsx`: remove `standalone` and `onLogout`. On mount and `onSubmitted`, `void refreshUser()`.

Keep `RequireApprovedOperator` so non-operators cannot open the page.

- [ ] **Step 4: `cd apps/web && npm test` PASS**

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/App.tsx \
  apps/web/src/pages/OperatorBlockers.tsx \
  apps/web/src/pages/OperatorNowReport.tsx \
  apps/web/src/pages/OperatorParks.tsx
git commit -m "fix(web): use one park context and put operator parks in the shell"
```

---

### Task 6: Privileged RBAC grants + tracker.write

**Files:**
- Modify: `apps/api/src/robopark_api/services/rbac.py`
- Modify: `apps/api/src/robopark_api/routers/admin_roles.py`
- Modify: `apps/api/src/robopark_api/routers/admin_users.py`
- Modify: `apps/api/src/robopark_api/services/tracker_policy.py`
- Test: `apps/api/tests/test_admin_roles.py`, `apps/api/tests/test_admin_user_access.py`, `apps/api/tests/test_tracker_policy.py`

**Interfaces:**
- Produces: `PRIVILEGED_PERMISSIONS: frozenset[str]` containing `nav.admin`, `nav.admin.tracker`, `nav.admin.emergency`, `roles.manage`, `users.manage`, `users.approve`, `parks.manage`
- Produces: `def privileged_grant_blocked(actor: User, existing: set[str], desired: set[str]) -> bool` — True when actor is not royal and `(desired - existing) & PRIVILEGED_PERMISSIONS` is non-empty
- HTTP: `403` with `detail="privileged_grant_forbidden"`
- `can_write_tracker(db, user)` is True only if `rbac.has_permission(db, user, PERMISSION_TRACKER_WRITE)` **and** (if role is mechanic) `mechanic_can_write` platform flag. Admin/royal keep write because the catalog grants `tracker.write`. Denying `tracker.write` via user override must disable write. Attach-without-write stays unchanged.

- [ ] **Step 1: Failing tests**

`test_admin_roles.py` — admin user (see `test_admin_user_access.test_admin_cannot_change_access_status` for the fixture pattern, login password `"secret"`):

```python
def test_admin_cannot_grant_nav_admin_to_operator(client, seed_royal, db_session):
    admin = User(
        username="admin-roles",
        password_hash=hash_password("secret"),
        role_id=role_id_for(db_session, "admin"),
        access_status="approved",
        is_active=True,
    )
    db_session.add(admin)
    db_session.commit()
    login_as(client, "admin-roles", "secret")
    roles = client.get("/admin/roles").json()
    operator = next(row for row in roles if row["slug"] == "operator")
    response = client.patch(
        f"/admin/roles/{operator['id']}",
        json={"permissions": operator["permissions"] + ["nav.admin"]},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "privileged_grant_forbidden"
```

Royal doing the same grant must stay 200.

`test_admin_user_access.py`: admin patching a mechanic with `permissions` that include `nav.admin` → 403 `privileged_grant_forbidden`. Royal adding `nav.analytics` to mechanic (existing test) still 200.

`test_tracker_policy.py`: operator with `tracker.write` denied via `set_user_effective_permissions` → `can_write_tracker` is False. Default operator → True. Mechanic with permission but `mechanic_can_write` False → False.

- [ ] **Step 2: Run the new tests — FAIL**

- [ ] **Step 3: Implement**

`rbac.py` — add the frozenset and helper next to `ALL_PERMISSIONS`.

`admin_roles.py` `_set_role_permissions`: it has no actor. Instead, in `create_role` / `update_role`, before `_set_role_permissions`, compute `existing = {p.key for p in role.permissions}` (empty set on create) and `desired = set(payload.permissions or [])`. If `privileged_grant_blocked(actor, existing, desired)`: 403.

`admin_users.py`: before `set_user_effective_permissions`, `existing = rbac.role_permission_keys(db, user)` (role baseline, not current effective — so admin cannot add privileged keys the role lacks). `desired = set(payload.permissions)`. Same 403. Apply on create and both patch paths that set permissions.

`tracker_policy.py`:

```python
def can_write_tracker(db: Session, user: User) -> bool:
    if not rbac.has_permission(db, user, rbac.PERMISSION_TRACKER_WRITE):
        return False
    if rbac.role_slug(user) == RoleSlug.MECHANIC:
        return settings_svc.tracker_policy_status(db)["mechanic_can_write"]
    return True
```

Do not change the `action == "attach"` early return.

- [ ] **Step 4: Run**

```bash
cd apps/api && .venv/bin/python -m pytest tests/test_admin_roles.py tests/test_admin_user_access.py tests/test_tracker_policy.py tests/test_tracker_actions.py -q
```

Expected: PASS. If an existing test granted `nav.admin` as admin, update that test to royal or drop the privileged key.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/robopark_api/services/rbac.py \
  apps/api/src/robopark_api/routers/admin_roles.py \
  apps/api/src/robopark_api/routers/admin_users.py \
  apps/api/src/robopark_api/services/tracker_policy.py \
  apps/api/tests/test_admin_roles.py \
  apps/api/tests/test_admin_user_access.py \
  apps/api/tests/test_tracker_policy.py
git commit -m "fix(rbac): block non-royal privileged grants and honor tracker.write"
```

---

### Task 7: Screenshot guard, hide stubs, docs, Home spinner, Register 409

**Files:**
- Modify: `apps/web/src/components/ScreenshotGuard/ScreenshotGuard.tsx`
- Modify: `apps/web/src/components/ScreenshotGuard/screenshotGuardLogic.ts`
- Modify: `apps/web/src/components/ScreenshotGuard/screenshotGuardLogic.test.ts`
- Modify: `apps/web/src/nav-permissions.ts` (create `apps/web/src/nav-permissions.test.ts` if missing)
- Modify: `apps/web/src/pages/Home.tsx`
- Modify: `apps/web/src/pages/Register.tsx`
- Modify: `README.md`, `deploy/README.md`

**Interfaces:**
- Screenshot overlay is **not** shown on `blur`, `visibilitychange`, `pagehide`, or `pageshow`. Keep keydown shortcuts, print, and protected copy/cut/context/drag/select/touch-on-media.
- `isScreenshotShortcut`: do **not** treat Ctrl+Shift+S (browser Save) as a screenshot. Keep PrintScreen; keep Meta+Shift+S (Win+Shift+S / some macOS tools); keep Meta+Shift+3/4/5/6; keep Meta+G game bar. `ctrlKey && shiftKey && key === 's'` must be false.
- `navItemsForPermissions` omits `item.stub === true`. Routes `/map`, `/learning`, `/help` stay registered.
- Home loading uses the same Spinner block as Login (`<main className="page page-auth"><Spinner label={ru.loading} /></main>`).
- Register: drop the 409 branch; 403 stays.
- Docs: empty `SECRET_KEY` does **not** store plaintext. API fail-closed (`MissingSecretKeyError`). Align `README.md` Security (~280) and `deploy/README.md` (~137).

- [ ] **Step 1: Failing tests**

`screenshotGuardLogic.test.ts` — add:

```ts
it('does not treat Ctrl+Shift+S as a screenshot', () => {
  expect(
    isScreenshotShortcut(keyEvent({ key: 's', shiftKey: true, ctrlKey: true })),
  ).toBe(false)
})

it('still treats Meta+Shift+S as a snipping shortcut', () => {
  expect(
    isScreenshotShortcut(keyEvent({ key: 's', shiftKey: true, metaKey: true })),
  ).toBe(true)
})
```

`nav-permissions.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import { navItemsForPermissions } from './nav-permissions'

it('hides stub nav items even when the permission is granted', () => {
  const items = navItemsForPermissions(['nav.map', 'nav.dashboard', 'nav.help'])
  expect(items.map((item) => item.id)).toEqual(['dashboard'])
})
```

- [ ] **Step 2: Run those vitest files — FAIL**

- [ ] **Step 3: Implement**

`isScreenshotShortcut` — replace the combined `meta || ctrl` + `s` check with meta-only for `s`. Keep `ctrl` out of the `s` branch.

`ScreenshotGuard.tsx` — remove `onBlur`, `onVisibilityChange`, `onPageHide`, `onPageShow` and their `addEventListener` / `removeEventListener` pairs.

`nav-permissions.ts`:

```ts
return ALL_NAV_ITEMS.filter(
  (item) => !item.stub && allowed.has(NAV_PERMISSION[item.id]),
)
```

`Home.tsx` — copy Login’s loading spinner (import `Spinner` + `ru`).

`Register.tsx` — delete the `status === 409` branch.

`README.md`: replace the plaintext paragraph with: without `SECRET_KEY` the API refuses to start / refuses to persist secrets (`MissingSecretKeyError`). Keep the Fernet generate snippet.

`deploy/README.md`: replace “stored as plaintext” with fail-closed language matching the code.

- [ ] **Step 4:**

```bash
cd apps/web && npx vitest run src/components/ScreenshotGuard/screenshotGuardLogic.test.ts src/nav-permissions.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/ScreenshotGuard/ScreenshotGuard.tsx \
  apps/web/src/components/ScreenshotGuard/screenshotGuardLogic.ts \
  apps/web/src/components/ScreenshotGuard/screenshotGuardLogic.test.ts \
  apps/web/src/nav-permissions.ts \
  apps/web/src/nav-permissions.test.ts \
  apps/web/src/pages/Home.tsx \
  apps/web/src/pages/Register.tsx \
  README.md \
  deploy/README.md
git commit -m "fix(web): tighten screenshot guard, hide stub nav, and correct SECRET_KEY docs"
```

---

## Coverage check

| Finding | Task |
|---|---|
| P1 abort does not stop runner | 1 |
| P1 Docker COPY data/ops | 2 |
| P1 every 401 is Emergency | 3 |
| P1 CSP dropped on index.html | 2 |
| P1 admin can grant nav.admin | 6 |
| P1 logout stays on gate pages | 4 |
| P1 operator parkId vs ParkProvider | 5 |
| P1 screenshot on blur/visibility | 7 |
| P2 cache on me 401 | 4 |
| P2 login 429 | 3 |
| P2 SECRET_KEY docs | 7 |
| P2 parks outside shell | 5 |
| P2 stub nav map/learning/help | 7 |
| P2 can_write_tracker ignores permission | 6 |
| P2 recentRobots on logout | 4 |
| P3 Home loading null | 7 |
| P3 Register 409 | 7 |
