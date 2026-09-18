# Minimal PWA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Installable, fast repeat loads of public static shell files without persisting user data or serving stale API responses.

**Architecture:** A build-generated versioned service worker precaches only entry JS/CSS, icons and an offline page. Navigation and API remain network-driven; runtime cache accepts only same-origin hashed `/assets/` GET responses, with bounded entries and old-version cleanup. Production registration is non-blocking and never forces an active page reload.

**Tech Stack:** React 19, Vite 8, native Service Worker/CacheStorage APIs, nginx, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-18-minimal-pwa-design.md`

## Global Constraints

- Never cache `/api/`, task/report/photo data, cookies, non-GET requests or cross-origin responses.
- HTML is network-only; offline navigation returns a neutral page with no user data.
- The current interface and client-side data revalidation remain unchanged.
- No new runtime dependencies; installation/update errors do not block the normal site.

---

### Task 1: Manifest and installable icon assets

**Files:** Create `apps/web/public/manifest.webmanifest`, `apps/web/public/pwa-icon-192.png`, `apps/web/public/pwa-icon-512.png`, `apps/web/public/offline.html`; modify `apps/web/index.html`; test `apps/web/e2e/app-shell.spec.ts` and a build-artifact check.

**Interfaces:** Manifest URL `/manifest.webmanifest`; icon URLs `/pwa-icon-192.png` and `/pwa-icon-512.png`; offline URL `/offline.html`.

- [ ] Write a Playwright test that requests the manifest and asserts exact `name`, `short_name`, `/` scope/start URL, `standalone`, 192/512 icons, and that each icon decodes as PNG at its declared size. It must fail while the manifest is absent.
  ```ts
  const manifest = await (await page.request.get('/manifest.webmanifest')).json()
  expect(manifest).toMatchObject({ name: 'Робопарк', short_name: 'Робопарк', start_url: '/', scope: '/', display: 'standalone' })
  for (const size of [192, 512]) {
    const icon = manifest.icons.find((item: { sizes: string }) => item.sizes === `${size}x${size}`)
    expect(icon.type).toBe('image/png')
    const response = await page.request.get(icon.src)
    expect(response.ok()).toBe(true)
    const png = await response.body()
    expect(png.subarray(0, 8).toString('hex')).toBe('89504e470d0a1a0a')
    expect([png.readUInt32BE(16), png.readUInt32BE(20)]).toEqual([size, size])
  }
  ```
- [ ] Run `npx playwright test e2e/app-shell.spec.ts -g 'PWA manifest' --reporter=line`; confirm 404/selector failure.
- [ ] Add manifest link/theme metadata and the manifest/offline assets. Mechanically rasterize the existing favicon to two PNG sizes, preserving its sign and colors; do not replace the user-facing design. The offline page contains only a short connection message and retry button.
  ```html
  <link rel="manifest" href="/manifest.webmanifest" />
  <meta name="theme-color" content="#243344" />
  ```
- [ ] Rerun the focused test and `npm run build`; require exit 0.

### Task 2: Safe, versioned static caching

**Files:** Create `apps/web/scripts/build-sw.mjs`, `apps/web/scripts/sw-template.js`, `apps/web/scripts/build-sw.test.mjs`; modify `apps/web/package.json`, `apps/web/nginx.conf`.

**Interfaces:** `buildServiceWorker(distDirectory)` writes `/sw.js` with a cache version derived from built entry file names/content. The worker handles only same-origin GET `/assets/*` and navigation; API and all other requests are untouched.

- [ ] Write a Node test using a temporary `dist` fixture with `index.html`, two hashed entry assets, icons and offline page. Assert generated worker precache list, content-derived version change, and missing-asset failure. Run `node --test scripts/build-sw.test.mjs`; see missing-function failure.
  ```js
  const first = await buildServiceWorker(tempDist)
  assert.match(first, /assets\/index-a1\.js/)
  await writeFile(join(tempDist, 'assets/index-a1.js'), 'different bytes')
  const second = await buildServiceWorker(tempDist)
  assert.notEqual(first, second)
  ```
- [ ] Write a worker behavior test with a fake service-worker scope/CacheStorage. Assert API/non-GET/cross-origin requests receive no `respondWith`, navigation uses network then offline page on failure, and hashed assets cache only successful same-origin responses. Run and see expected failures.
  ```js
  for (const request of ['/api/auth/me', '/api/tracker/issues']) {
    const event = dispatchFetch(request, { method: 'GET' })
    assert.equal(event.responded, false)
  }
  assert.equal(dispatchFetch('/assets/index-a1.js', { method: 'POST' }).responded, false)
  assert.equal(dispatchFetch('https://tile.openstreetmap.org/1/1/1.png').responded, false)
  ```
- [ ] Implement the generator/template, invoke generator after `vite build`, cap the runtime asset cache (100 entries), clean old named caches on activation, and leave waiting worker inactive while existing clients run. Add nginx no-store/no-cache policy for worker, manifest and offline page, preserving security headers.
  ```js
  // package.json build script
  "build": "tsc -b && vite build && node scripts/build-sw.mjs"
  // worker fetch gate: only the following requests call respondWith
  const eligible = request.method === 'GET' && url.origin === self.location.origin
    && (request.mode === 'navigate' || /^\/assets\/[A-Za-z0-9_-]+\.[a-z0-9]+$/.test(url.pathname))
  ```
- [ ] Run Node tests and `npm run build`, then confirm `dist/sw.js` changes when an entry asset changes.

### Task 3: Production-only registration and regression tests

**Files:** Create `apps/web/src/pwa/registerServiceWorker.ts`, `apps/web/src/pwa/registerServiceWorker.test.ts`; modify `apps/web/src/main.tsx`, `apps/web/e2e/operational/task-lifecycle.spec.ts`, `apps/web/e2e/operational/diagnostic-rules.spec.ts`.

**Interfaces:** `registerServiceWorker()` registers `/sw.js` in production on secure context, requests a version check on focus/visibility return, and quietly degrades if unsupported or failed.

- [ ] Write Vitest cases for production supported browser, dev/unsupported browser, registration error, and focus recheck; run targeted tests and see red.
  ```ts
  const register = vi.fn().mockResolvedValue({ update: vi.fn() })
  await registerServiceWorker({ production: true, navigator: { serviceWorker: { register } } })
  expect(register).toHaveBeenCalledWith('/sw.js', { scope: '/' })
  ```
- [ ] Implement registration without force reload or `skipWaiting`; rerun targeted tests and build.
- [ ] Verify the already edited E2E selectors: follow the overview link after login and assert the auto-selected robot photo rather than the removed manual view controls. Run `npx playwright test e2e/operational/task-lifecycle.spec.ts e2e/operational/diagnostic-rules.spec.ts --workers=1 --reporter=line` and require green; investigate any remaining failure before altering it.
- [ ] Run `npm test -- --run`, the targeted E2E command above, `npm run build`, `npm run lint`, `node --test scripts/build-sw.test.mjs` and `git diff --check`. Report real HTTPS installation as unverified until the Tuna tunnel is restored.
