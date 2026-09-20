# Task 6 report: browser CI, soak and production PWA smoke

## Result

- Regular Playwright discovery excludes `operational/soak.spec.ts`.
- The soak suite is opt-in and its launcher refuses to start unless both a positive duration and an output path are supplied.
- `test:e2e:pwa` builds and serves the production `dist` through a dedicated Playwright config.
- The production smoke uses a deterministic same-origin fixture server, verifies successful controlled API/private attachment responses, then proves their URLs and bodies are absent from every Cache Storage entry.
- Offline navigation asserts an editable rendered login form and enabled action after reload under service-worker control, rather than accepting raw `index.html` as success.
- Soak duration validation numerically rejects zero spellings, negative and non-finite values before Docker or workspace setup.
- CI runs regular browser journeys and the production PWA smoke as separate steps.

## Changed files

- `.github/workflows/ci.yml`
- `apps/web/package.json`
- `apps/web/playwright.config.ts`
- `apps/web/playwright.pwa.config.ts`
- `apps/web/scripts/playwright-linux.sh`
- `apps/web/scripts/serve-pwa-fixture.mjs`
- `apps/web/scripts/build-sw.test.mjs`
- `apps/web/e2e/operational/soak.spec.ts`
- `apps/web/e2e-production/pwa-production.spec.ts`

## Verification

- `node --test scripts/build-sw.test.mjs` — 7 passed, including duration boundary cases.
- `ROBOPARK_E2E_SUITE=soak ROBOPARK_SOAK_DURATION_SECONDS=1 ROBOPARK_SOAK_OUTPUT=tmp/soak.json npx playwright test --list --project=chromium` — exactly 1 soak test discovered.
- `bash -n scripts/playwright-linux.sh` — passed.
- `npx oxlint playwright.pwa.config.ts e2e-production/pwa-production.spec.ts scripts/serve-pwa-fixture.mjs` — passed (the initial scoped lint also covered the regular config and soak spec).
- `npm run test:e2e:pwa` — production build passed; 1 Chromium PWA smoke passed in 0.85 s.
- `git diff --check` — passed.

No soak, regular E2E suite, full web suite, Docker build, load test, OTA or signature work was run.
