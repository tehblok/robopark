# Remember me — implementation plan

> Execute task-by-task. Tests first where the suite already covers auth.

**Goal:** Login checkbox «Запомнить меня»: idle 3 days, session cookie if unchecked, 30-day persistent cookie if checked.

**Architecture:** Login accepts `remember_me`. Session `expires_at` is an idle deadline, slid in `require_user` (throttled). Persistent cookies use absolute TTL as `max_age`; session cookies omit `max_age`. Auth is still the hashed cookie in `sessions`.

**Tech Stack:** FastAPI, SQLAlchemy, httpOnly cookie, React login form.

## Global Constraints

- Russian UI copy only.
- Do not persist the checkbox in localStorage.
- Do not commit `apps/api/data/ops/` or secrets.
- Keep httponly + SameSite=lax.

## File map

- `apps/api/src/robopark_api/config.py` — idle + absolute TTL
- `apps/api/src/robopark_api/schemas.py` — `remember_me` on login
- `apps/api/src/robopark_api/routers/auth.py` — cookie + initial `expires_at`
- `apps/api/src/robopark_api/deps.py` — idle/absolute check + throttled slide
- `apps/api/tests/test_auth.py` + `conftest.py` — cookie and idle tests
- `apps/web/src/pages/Login.tsx`, `auth.tsx`, `auth-context.ts`, `api.ts`, `ChangePassword.tsx`, `i18n/ru.ts`, `index.css`

---

## Task 1: API session idle + remember_me cookie

- [x] Tests: default login has no Max-Age / Max-Age=0; `remember_me: true` has Max-Age ≈ 30d; idle-expired session 401; slid session still valid; absolute-expired 401 even if expires_at is in the future.
- [x] Implement settings, login cookie, `require_user` slide.
- [x] Update `.env.example` (and host.env.example comment if TTL was documented).

## Task 2: Login UI + change-password keep session

- [x] Checkbox + copy on login; pass `remember_me` through api/auth.
- [x] ChangePassword: `api.me()` after success, not re-login.
- [x] Verify API tests + web typecheck/tests for touched files.
