# Task 9 frontend/docs review fix

## Scope

- Production PWA smoke now performs a real two-worker upgrade: v1 controls the
  page, v2 reaches `waiting`, the empty offline action store is verified,
  `ACTIVATE_WHEN_SAFE` is posted to the real waiting worker, and the test checks
  `controllerchange`, reload, the v2 shell marker, old-cache removal and private
  request exclusion.
- Admin user activity responses expose typed IP-location source and availability
  separately from the optional location string. The UI distinguishes disabled,
  missing-IP and provider-unavailable states without exposing a stored location
  while the provider is disabled.
- Acceptance documents label prior full/load/host/build results as historical and
  the current source as unverified by those records.

## Commits

- `52e8a9a test(pwa): verify safe production worker upgrades`
- `c3a2570 fix(admin): explain IP location availability`
- Documentation/report commit: this file's commit.

## TDD evidence

- PWA smoke failed first because the fixture had no v1 marker, then because the
  worker caches were not version-distinct.
- API tests failed first because `location_source` was absent.
- Admin UI test failed first because disabled location was rendered as generic
  “Недоступно”.

## Fresh targeted verification

- `apps/api/.venv/bin/pytest -q apps/api/tests/test_user_activity.py` — 9 passed.
- `apps/api/.venv/bin/ruff check apps/api/src/robopark_api/routers/admin_users.py apps/api/tests/test_user_activity.py` — passed.
- `npm test -- --run src/components/admin/AdminUsersPanel.test.tsx src/pwa/registerServiceWorker.test.ts` — 8 passed.
- `npx tsc -b --pretty false` — passed.
- `npx oxlint ...` for changed web files — passed.
- `npm run check-nav` — 31 route ids, passed.
- `node --test scripts/build-sw.test.mjs` — 7 passed.
- `npx playwright test --config=playwright.pwa.config.ts e2e-production/pwa-production.spec.ts` — 1 passed in Chromium.
- `git diff --check` — passed before final commits.

Per user instruction, full API/web, regular E2E, PostgreSQL, Docker, load, soak,
installer/VM and OTA checks were not run. No signature behavior was changed.
