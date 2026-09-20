# Task 6 report: browser CI, soak and production PWA smoke

## Result

- Regular Playwright discovery excludes `operational/soak.spec.ts`.
- The soak suite is opt-in and its launcher refuses to start unless both a positive duration and an output path are supplied.
- `test:e2e:pwa` builds and serves the production `dist` through a dedicated Playwright config.
- The production smoke verifies real service-worker registration/control, offline application-shell navigation, and absence of API/private attachment responses from Cache Storage.
- CI runs regular browser journeys and the production PWA smoke as separate steps.

## Changed files

- `.github/workflows/ci.yml`
- `apps/web/package.json`
- `apps/web/playwright.config.ts`
- `apps/web/playwright.pwa.config.ts`
- `apps/web/scripts/playwright-linux.sh`
- `apps/web/scripts/build-sw.test.mjs`
- `apps/web/e2e/operational/soak.spec.ts`
- `apps/web/e2e-production/pwa-production.spec.ts`

## Verification

- `node --test scripts/build-sw.test.mjs` — 6 passed.
- `ROBOPARK_E2E_SUITE=soak ROBOPARK_SOAK_DURATION_SECONDS=1 ROBOPARK_SOAK_OUTPUT=tmp/soak.json npx playwright test --list --project=chromium` — exactly 1 soak test discovered.
- `bash -n scripts/playwright-linux.sh` — passed.
- `npx oxlint playwright.config.ts playwright.pwa.config.ts e2e/operational/soak.spec.ts e2e-production/pwa-production.spec.ts` — passed.
- `npm run test:e2e:pwa` — production build passed; 1 Chromium PWA smoke passed in 1.3 s.
- `git diff --check` — passed.

No soak, regular E2E suite, full web suite, Docker build, load test, OTA or signature work was run.
