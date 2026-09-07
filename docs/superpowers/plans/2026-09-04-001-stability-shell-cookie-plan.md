# Stability, Shell, and Emergency Cookie Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep navigation responsive, remove the Robopark wordmark, and validate an Emergency cookie before replacing the working secret.

**Architecture:** The shell owns navigation independently from route data; resource consumers discard late results. Cookie validation is a candidate transaction: probe Emergency first, persist only on success, and expose an explicit validation state.

**Tech Stack:** React 19, React Router 7, Vitest, Playwright, FastAPI, SQLAlchemy, pytest.

**Spec:** `docs/superpowers/specs/2026-09-04-operations-interface-redesign-design.md`

## Global Constraints

- The app label is the selected park name only; no «РобоПарк» wordmark remains.
- Navigation stays usable while Tracker or Emergency is slow.
- Invalid and unavailable candidate cookies never replace the stored cookie.
- Secrets never appear in response bodies, audit text, or test snapshots.
- Light and dark themes and 320–1440 px layouts remain supported.

---

### Task 1: Route-independent loading lifecycle

**Files:**
- Modify: `apps/web/src/lib/resource.ts`
- Modify: `apps/web/src/lib/resource.test.ts`
- Modify: `apps/web/e2e/operational/workspace-navigation.spec.ts`

**Interfaces:**
- Produces: `useCachedResource(key, loader, options)` whose observer detaches on unmount and ignores late results.
- Produces: a browser regression proving `/robots` opens while `/operations/overview` is held pending.

- [ ] **Step 1: Write the failing hook regression**

```tsx
it('does not commit a late result after the owner unmounts', async () => {
  const pending = deferred<{ value: string }>()
  const view = render(<Owner resourceKey="slow" loader={() => pending.promise} />)
  view.unmount()
  pending.resolve({ value: 'late' })
  await pending.promise
  expect(resourceStore.get('slow')).toBeUndefined()
  expect(inFlight.isActive).toBe(false)
})
```

- [ ] **Step 2: Run the hook test and confirm the late result is currently committed**

Run: `cd apps/web && npm test -- src/lib/resource.test.ts`

- [ ] **Step 3: Add an owner generation and unmount cleanup**

Implement a mounted generation in `useCachedResource`; before `resourceStore.set`, `setError`, and `setIsRevalidating`, require the captured generation to still be current. Always balance `inFlight.begin/end` in `finally`.

- [ ] **Step 4: Add and run the browser regression**

Intercept `**/api/operations/overview**` with an unresolved promise, open `/overview`, click the «Роботы» navigation link, and assert URL `/robots` plus heading «Роботы» within 1 second.

Run: `cd apps/web && npx playwright test e2e/operational/workspace-navigation.spec.ts`

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/resource.ts apps/web/src/lib/resource.test.ts apps/web/e2e/operational/workspace-navigation.spec.ts
git commit -m "fix(web): detach route resources from navigation"
```

### Task 2: Park-only shell identity

**Files:**
- Modify: `apps/web/src/app/shell/AppShell.tsx`
- Modify: `apps/web/src/app/shell/AppShell.css`
- Modify: `apps/web/src/app/shell/AppShell.test.tsx`
- Modify: `apps/web/src/components/auth/AuthLayout.tsx`
- Modify: `apps/web/src/pages/AuthPages.test.tsx`
- Modify: `apps/web/src/i18n/ru.ts`
- Modify: `apps/web/index.html`

**Interfaces:**
- Produces: `ParkIdentity` showing only `selectedPark.name`.
- Produces: an accessible `button` + listbox selector for operator/admin/royal with more than one park.

- [ ] **Step 1: Write failing tests for the wordmark and role behavior**

Assert that «РобоПарк» is absent, «Next» is present, admin can open «Сменить парк», and mechanic sees no park-switch button.

- [ ] **Step 2: Run tests and confirm failure on the existing base wordmark**

Run: `cd apps/web && npm test -- src/app/shell/AppShell.test.tsx src/pages/AuthPages.test.tsx`

- [ ] **Step 3: Replace `ParkWordmark` with `ParkIdentity`**

Render the park name as the button label. Keep `park` in the URL and session storage through `setParkId`; use the existing role set `operator/admin/royal`. Public auth copy becomes «Управление парком».

- [ ] **Step 4: Search for legacy visible branding and run tests**

Run: `rg -n "РобоПарк|Робопарк" apps/web/src apps/web/index.html -g '!*.test.*'`
Expected: no user-visible occurrences.

Run: `cd apps/web && npm test -- src/app/shell/AppShell.test.tsx src/pages/AuthPages.test.tsx`

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/app/shell apps/web/src/components/auth/AuthLayout.tsx apps/web/src/pages/AuthPages.test.tsx apps/web/src/i18n/ru.ts apps/web/index.html
git commit -m "feat(web): make park name the application identity"
```

### Task 3: Candidate Emergency cookie validation API

**Files:**
- Modify: `apps/api/src/robopark_api/schemas.py`
- Modify: `apps/api/src/robopark_api/routers/admin_settings.py`
- Modify: `apps/api/src/robopark_api/services/platform_settings.py`
- Modify: `apps/api/src/robopark_api/services/emergency_cache.py`
- Modify: `apps/api/tests/test_platform_settings.py`
- Modify: `apps/api/tests/test_admin_emergency.py`

**Interfaces:**
- Produces: `PUT /admin/settings/emergency-cookie` body `{cookie, robot_number}`.
- Produces: `POST /admin/settings/emergency-cookie/check` body `{robot_number?: string}`.
- Produces: status enum `unchecked | valid | invalid | unavailable`, `checked_at`, and `checked_robot`.

- [ ] **Step 1: Write failing API tests**

```python
def test_invalid_candidate_does_not_replace_working_cookie(client, db_session, seed_royal, monkeypatch):
    settings_svc.set_setting(db_session, settings_svc.EMERGENCY_COOKIE_KEY, "working")
    monkeypatch.setattr(emergency_client, "fetch_robot_payload", Mock(side_effect=EmergencyAuthError()))
    response = client.put("/admin/settings/emergency-cookie", json={"cookie": "bad", "robot_number": "A2378"})
    assert response.status_code == 401
    assert settings_svc.get_emergency_cookie(db_session) == "working"

def test_valid_candidate_is_saved_after_probe(client, db_session, seed_royal, monkeypatch):
    monkeypatch.setattr(emergency_client, "fetch_robot_payload", Mock(return_value={"vin": "YASADR00000002378"}))
    response = client.put("/admin/settings/emergency-cookie", json={"cookie": "candidate", "robot_number": "A2378"})
    assert response.status_code == 200
    assert settings_svc.get_emergency_cookie(db_session) == "candidate"
    assert response.json()["emergency_cookie_status"] == "valid"
```

- [ ] **Step 2: Run tests and confirm the invalid candidate currently replaces the secret**

Run: `cd apps/api && uv run --frozen --extra dev pytest tests/test_admin_emergency.py tests/test_platform_settings.py -q`

- [ ] **Step 3: Implement candidate validation and metadata**

Normalize with `emergency_vin.normalize_robot_id`, call `emergency_client.fetch_robot_payload(cookie=payload.cookie, vin=vin)`, and persist only in the success branch. Store status, ISO timestamp, and short robot number as non-secret platform settings. Map auth failure to 401 and upstream failure to 503.

- [ ] **Step 4: Implement saved-cookie recheck**

Resolve the explicit robot first, otherwise the last keepalive ring entry. Return 422 `emergency_probe_required` if neither exists. Clear `emergency_cache` after successful replacement.

- [ ] **Step 5: Run API tests and commit**

Run: `cd apps/api && uv run --frozen --extra dev pytest tests/test_admin_emergency.py tests/test_platform_settings.py tests/test_emergency_keepalive.py -q`

```bash
git add apps/api/src/robopark_api apps/api/tests/test_admin_emergency.py apps/api/tests/test_platform_settings.py
git commit -m "fix(api): verify Emergency cookie before activation"
```

### Task 4: Cookie validation UI and final slice verification

**Files:**
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/pages/Admin.tsx`
- Create: `apps/web/src/pages/Admin.test.tsx`
- Modify: `apps/web/src/i18n/errors.ts`
- Create: `apps/web/e2e/operational/admin-settings.spec.ts`

**Interfaces:**
- Consumes: the candidate and recheck endpoints from Task 3.
- Produces: cookie status, probe robot input, «Сохранить и проверить» and «Проверить текущую» actions.

- [ ] **Step 1: Write failing UI tests**

Assert that `invalid` renders «Недействительна», `unavailable` renders a retryable warning, save requires both cookie and robot, and an API failure keeps the cookie input for correction.

- [ ] **Step 2: Run the tests and confirm the controls are missing**

Run: `cd apps/web && npm test -- src/pages/Admin.test.tsx`

- [ ] **Step 3: Implement typed API and the integration panel**

Extend `IntegrationSettings` with validation metadata; change `setEmergencyCookie(cookie, robotNumber)` and add `checkEmergencyCookie(robotNumber?)`. Keep Tracker and Emergency actions independently busy so one integration does not disable the other.

- [ ] **Step 4: Run focused and repository checks**

Run: `cd apps/web && npm test -- src/pages/Admin.test.tsx src/app/shell/AppShell.test.tsx src/lib/resource.test.ts`

Run: `./scripts/verify.sh api`

Run: `./scripts/verify.sh web`

- [ ] **Step 5: Run responsive browser tests and commit**

Run: `cd apps/web && npx playwright test e2e/operational/workspace-navigation.spec.ts e2e/operational/admin-settings.spec.ts`

```bash
git add apps/web/src apps/web/e2e/operational
git commit -m "feat(web): expose verified Emergency credentials"
```
