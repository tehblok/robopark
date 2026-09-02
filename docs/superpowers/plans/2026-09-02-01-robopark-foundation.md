# Robopark Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stabilize authentication and build the accessible, themeable routing and design-system foundation on which every redesigned Robopark screen will run.

**Architecture:** Keep the existing React/FastAPI application running while introducing a declarative route manifest, a single access-policy layer, a new responsive shell, and focused design-system modules. Existing pages remain mounted through compatibility routes until their domain plans replace them; the API remains authoritative for permissions.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, pytest, React 19, TypeScript 6, React Router 7, Vite 8, Vitest, Testing Library, CSS custom properties, Lucide React, Playwright, axe-core.

**Spec:** `docs/superpowers/specs/2026-09-02-robopark-product-redesign-design.md`

## Global Constraints

- The main UX order is exactly: state → risk → next action.
- User-facing copy says «Проверка робота»; `emergency` may remain only in compatibility API and legacy path names.
- Light, dark, and system theme preferences are required; light and dark share one semantic-token contract.
- Desktop offers comfortable and compact density; widths through 899 px always resolve to comfortable while retaining the desktop preference.
- Compact phone is at most 599 px, phone/tablet is 600–899 px, split tablet is 900–1199 px, and desktop is at least 1200 px.
- Interactive targets are at least 44×44 px; body/control text is at least 14 px; mobile form controls are at least 16 px.
- Text-bearing color combinations must meet WCAG AA; status must never be encoded only by color.
- Stable API contracts stay intact unless a task includes a named backend test and migration.
- No Yandex logos, wordmarks, fonts, copied geometry, or recognizable commercial robot silhouette.
- E2E fixtures are deterministic mocks; they never read developer runtime data, credentials, cookies, or secrets.
- Root `apps/web/src/index.css` is transitional legacy CSS and loads before new design-system CSS until Plan 05 removes it.
- Every task has a mandatory index gate after its scoped `git add` and before `git commit`: run `git diff --cached --check`, inspect `git diff --cached --name-only`, and compare the result with that task's exact `Files`/intentional-snapshot allowlist. If any pre-existing, unrelated, runtime, credential, database, log, trace, or generated-download path is staged, stop without unstaging or overwriting another contributor's work.

---

## Plan Boundary and Deliverables

This is Plan 01 and must be executed before Plans 02–05. It deliberately does not redesign domain content. It produces these stable interfaces:

- `routeManifest.ts`: route metadata and navigation types;
- `accessPolicy.ts`: access, landing, and navigation derivation;
- `AppRouter.tsx`: the only React-element registry for routes;
- `ThemeProvider.tsx`: system/light/dark theme plus responsive comfortable/compact density preference;
- `Icon.tsx`, `Button.tsx`, `StatusBadge.tsx`, `PageLayout.tsx`, `FormField.tsx`;
- `AsyncState.tsx`, `Dialog.tsx`, `BottomSheet.tsx`, `ConfirmDialog.tsx`, `Tabs.tsx`;
- a responsive `AppShell` that consumes only route and design-system contracts;
- Playwright/axe helpers reused by all later domain plans.

The complete redesign is intentionally split into five sequential, independently verifiable plans. Execute them in filename order so shared `RouteManifest`, `AppRouter`, `api.ts`, and test-harness edits are based on the preceding green commit:

1. [`01 Foundation`](2026-09-02-01-robopark-foundation.md) — auth stabilization, tokens, themes, primitives, route/access policy, shell, park scope, and browser harness.
2. [`02 Operational Core`](2026-09-02-02-robopark-operational-core.md) — Overview, Work, Robots, Robot detail, and «Проверка робота».
3. [`03 Reports and Analytics`](2026-09-02-03-robopark-reports-analytics.md) — report queue/detail/composer and honest analytics over existing history.
4. [`04 Administration and Onboarding`](2026-09-02-04-robopark-administration-onboarding.md) — auth/access states and all seven administration modules.
5. [`05 Migration and Quality`](2026-09-02-05-robopark-migration-quality.md) — global overlays, legacy removal, full acceptance matrix, bundle/deployment gates, and documentation.

Each completion gate is a hard checkpoint: do not begin the next file while the current gate is red. Owner-supplied robot photography remains a separately approved asset input; every plan stays complete with the original schematic fallback.

## File Responsibility Map

### API

- Modify `apps/api/src/robopark_api/db.py`: make request DB dependency teardown context-safe.
- Modify `apps/api/tests/test_auth.py`: reproduce unauthenticated `/auth/me` with the real dependency.

### Web application foundation

- Create `apps/web/src/app/routing/routeManifest.ts`: route metadata only, never React elements.
- Create `apps/web/src/app/routing/accessPolicy.ts`: pure access and navigation functions.
- Create `apps/web/src/app/routing/RouteGate.tsx`: authenticated prerequisite and permission guard.
- Create `apps/web/src/app/routing/AppRouter.tsx`: route-to-element registry and rendering.
- Create `apps/web/src/test/renderApp.tsx`: deterministic router/auth render helper.
- Create `apps/web/src/app/park/parkScope.ts` and `ParkScopeProvider.tsx`: URL-backed park scope.
- Create `apps/web/src/app/park/ParkScopeProvider.test.tsx`: URL/storage/lock behavior.
- Create `apps/web/src/app/shell/AppShell.tsx` and `AppShell.css`: responsive application chrome.
- Modify `apps/web/src/App.tsx`: reduce to the `AppRouter` entry point.
- Modify `apps/web/src/main.tsx`: mount ThemeProvider and load new CSS after legacy CSS.
- Modify `apps/web/src/i18n/ru.ts`: canonical navigation, theme, and density copy.
- Modify `apps/web/scripts/check-nav.mjs`: validate manifest IDs against the router registry.
- Modify `apps/web/src/ParkProvider.tsx` and `park-context.ts`: temporary compatibility re-exports.

### Design system

- Create `apps/web/src/design-system/styles/tokens.css`, `base.css`, `layout.css`, `index.css`.
- Create `apps/web/src/design-system/theme/theme.ts`, `ThemeProvider.tsx`, `ThemeProvider.test.tsx`.
- Modify `apps/web/index.html`: resolve stored/system theme and responsive density in `<head>` before the first paint.
- Create `apps/web/src/design-system/icons/Icon.tsx`, `Icon.test.tsx`.
- Create `apps/web/src/design-system/actions/Button.tsx`, `Button.css`, `Button.test.tsx`.
- Create `apps/web/src/design-system/status/StatusBadge.tsx`, `StatusBadge.css`.
- Create `apps/web/src/design-system/layout/PageLayout.tsx`, `PageLayout.css`.
- Create `apps/web/src/design-system/forms/FormField.tsx`, `FormField.css`.
- Create `apps/web/src/design-system/feedback/AsyncState.tsx`, `AsyncState.css`, `AsyncState.test.tsx`.
- Create `apps/web/src/design-system/overlays/Dialog.tsx`, `BottomSheet.tsx`, `ConfirmDialog.tsx`, `overlays.css`, `Dialog.test.tsx`, `ConfirmDialog.test.tsx`.
- Create `apps/web/src/design-system/navigation/Tabs.tsx`, `Tabs.css`, `Tabs.test.tsx`.
- Create `apps/web/scripts/check-contrast.mjs`: enforce key semantic-token contrast pairs.

### Browser verification

- Modify `apps/web/package.json` and `apps/web/package-lock.json`: add Lucide, Testing Library user-event, Playwright, and axe.
- Create `apps/web/playwright.config.ts`.
- Create `apps/web/e2e/support/mockApi.ts`, `assertA11y.ts`, and `users.ts`.
- Create `apps/web/e2e/app-shell.spec.ts` and its committed screenshot baselines.
- Modify `.github/workflows/ci.yml`: install Chromium and run browser checks after repository verification.

### Existing compatibility files

- Delete `apps/web/src/theme.ts` only after every import uses `design-system/theme`.
- Keep `apps/web/src/nav.ts`, `apps/web/src/nav-permissions.ts`, and their tests through Task 6; Task 8 deletes them only after the new shell has no imports.
- Keep `apps/web/src/components/AppShell.tsx` as a one-line re-export during Plan 01; Plan 05 removes it after all imports migrate.

---

### Task 1: Make the request DB dependency context-safe

**Files:**

- Modify: `apps/api/src/robopark_api/db.py`
- Modify: `apps/api/tests/test_auth.py`

**Interfaces:**

- Consumes: `RequestSession`, `bind_request_session`, and `reset_request_session` from `robopark_api.db`.
- Produces: `async def get_db() -> AsyncGenerator[Session, None]` with setup and teardown in one async context.

- [ ] **Step 1: Add a regression test that uses the real `get_db` dependency**

Add these imports and test to `apps/api/tests/test_auth.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from robopark_api import db as db_module
from robopark_api.config import get_settings
from robopark_api.routers import auth as auth_router


def test_me_without_cookie_unwinds_real_db_dependency_in_same_context(
    db_engine, test_settings, monkeypatch
):
    monkeypatch.setattr(
        db_module,
        "SessionLocal",
        sessionmaker(bind=db_engine, future=True),
    )
    app = FastAPI()
    app.include_router(auth_router.router)
    app.dependency_overrides[get_settings] = lambda: test_settings

    with TestClient(app, raise_server_exceptions=False) as isolated_client:
        response = isolated_client.get("/auth/me")

    assert response.status_code == 401
    assert response.json()["detail"] == "Unauthorized"
```

Do not override `get_db` in this test; that is the behavior under regression. Patching only the module-level `SessionLocal` factory keeps the real dependency lifecycle while guaranteeing the test opens the temporary `db_engine`, never the developer runtime database.

- [ ] **Step 2: Run the isolated test and verify the production failure**

Run:

```bash
cd apps/api
uv run --frozen --extra dev pytest -q tests/test_auth.py::test_me_without_cookie_unwinds_real_db_dependency_in_same_context
```

Expected before the fix: FAIL because the response is `500`, with the server log showing a `ContextVar` token reset from a different context.

- [ ] **Step 3: Convert `get_db` to an async generator**

Replace the import and function in `apps/api/src/robopark_api/db.py`:

```python
from collections.abc import AsyncGenerator


async def get_db() -> AsyncGenerator[Session, None]:
    wrapper = RequestSession()
    token = bind_request_session(wrapper)
    try:
        yield wrapper  # type: ignore[misc]
    finally:
        wrapper.close()
        reset_request_session(token)
```

Keep `RequestSession`, `release_request_session`, and their public signatures unchanged.

- [ ] **Step 4: Verify the regression and the whole API suite**

Run:

```bash
cd apps/api
uv run --frozen --extra dev pytest -q tests/test_auth.py::test_me_without_cookie_unwinds_real_db_dependency_in_same_context
uv run --frozen --extra dev pytest -q tests/test_session_release.py tests/test_auth.py
uv run --frozen --extra dev ruff check src/robopark_api/db.py tests/test_auth.py
uv run --frozen --extra dev ruff format --check src/robopark_api/db.py tests/test_auth.py
```

Expected: all selected tests pass, Ruff reports no issues, and `/auth/me` returns `401` without a cookie.

- [ ] **Step 5: Commit the stabilization fix**

```bash
git add apps/api/src/robopark_api/db.py apps/api/tests/test_auth.py
git commit -m "fix(api): make request session teardown context-safe"
```

---

### Task 2: Add semantic tokens, theme state, and responsive density

**Files:**

- Create: `apps/web/src/design-system/styles/tokens.css`
- Create: `apps/web/src/design-system/styles/base.css`
- Create: `apps/web/src/design-system/styles/layout.css`
- Create: `apps/web/src/design-system/styles/index.css`
- Create: `apps/web/src/design-system/styles/typography.test.ts`
- Create: `apps/web/src/design-system/theme/theme.ts`
- Create: `apps/web/src/design-system/theme/ThemeProvider.tsx`
- Create: `apps/web/src/design-system/theme/ThemeProvider.test.tsx`
- Create: `apps/web/scripts/check-contrast.mjs`
- Modify: `apps/web/index.html`
- Modify: `apps/web/src/main.tsx`
- Modify: `apps/web/src/components/AppShell.tsx`
- Modify: `apps/web/package.json`
- Modify: `apps/web/package-lock.json`
- Delete: `apps/web/src/theme.ts`

**Interfaces:**

- Produces: `ThemePreference = 'system' | 'light' | 'dark'`, `ResolvedTheme = 'light' | 'dark'`, `DensityPreference = 'comfortable' | 'compact'`, `readThemePreference`, `resolveTheme`, `applyTheme`, `readDensityPreference`, `resolveDensity`, `applyDensity`, `ThemeProvider`, and `useTheme`.
- Storage keys: exactly `robopark-theme` and `robopark-density`.
- DOM contract: `document.documentElement.dataset.theme` and `document.documentElement.style.colorScheme` are always resolved to `light` or `dark`; `document.documentElement.dataset.density` is always the resolved `comfortable` or `compact` density.
- Density contract: the stored desktop preference is independent from theme. At widths `<= 899px`, resolved density is always `comfortable`; when the viewport becomes wider again, the persisted desktop preference is restored without reload. Compact mode is a desktop information-density choice, not a way to shrink phone touch targets.
- Typography contract: the application serves one variable `Manrope Variable` family from its own Vite bundle through `@fontsource-variable/manrope`; no runtime request may target Google Fonts or another font CDN, and the bundled face must load Cyrillic glyphs.

- [ ] **Step 1: Write pure theme and provider tests**

Create `apps/web/src/design-system/theme/ThemeProvider.test.tsx`:

```tsx
import { readFileSync } from 'node:fs'
import { act, renderHook } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ThemeProvider, useTheme } from './ThemeProvider'
import { readDensityPreference, readThemePreference, resolveDensity, resolveTheme } from './theme'

describe('theme', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.removeAttribute('data-theme')
    document.documentElement.removeAttribute('data-density')
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
      matches: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }))
  })

  it('resolves system preference from prefers-color-scheme', () => {
    expect(resolveTheme('system', true)).toBe('dark')
    expect(resolveTheme('system', false)).toBe('light')
  })

  it('falls back to system for an invalid stored value', () => {
    localStorage.setItem('robopark-theme', 'blue')
    expect(readThemePreference()).toBe('system')
  })

  it('keeps a desktop density preference but forces comfortable density on a phone', () => {
    localStorage.setItem('robopark-density', 'compact')
    expect(readDensityPreference()).toBe('compact')
    expect(resolveDensity('compact', false)).toBe('compact')
    expect(resolveDensity('compact', true)).toBe('comfortable')
  })

  it('boots the resolved theme before the application module', () => {
    const html = readFileSync(new URL('../../../index.html', import.meta.url), 'utf8')
    const bootstrap = html.indexOf("localStorage.getItem('robopark-theme')")
    const application = html.indexOf('/src/main.tsx')
    expect(bootstrap).toBeGreaterThan(0)
    expect(application).toBeGreaterThan(bootstrap)
  })

  it('persists an explicit preference and applies its resolved theme', () => {
    const wrapper = ({ children }: PropsWithChildren) => (
      <ThemeProvider>{children}</ThemeProvider>
    )
    const { result } = renderHook(() => useTheme(), { wrapper })

    act(() => result.current.setPreference('light'))

    expect(localStorage.getItem('robopark-theme')).toBe('light')
    expect(document.documentElement.dataset.theme).toBe('light')
  })
})
```

Add a provider-level density test with query-specific `matchMedia` doubles: start wider than `899px`, choose `compact`, and assert storage plus `data-density="compact"`; dispatch the narrow-query change and assert `data-density="comfortable"`; dispatch the wide change and assert compact is restored while `robopark-density` never changes. The same test must prove that changing density does not rewrite `robopark-theme`. Add a storage-denied regression that makes `getItem`/`setItem` throw `SecurityError`, asserts system/comfortable defaults, and proves both in-memory selectors still update the DOM without crashing.

Create `apps/web/src/design-system/styles/typography.test.ts` as a filesystem contract test. It must read `styles/index.css`, `tokens.css`, `base.css`, `package.json`, and the lockfile and assert that:

```ts
expect(indexCss).toContain("@import '@fontsource-variable/manrope/index.css'")
expect(tokensCss).toContain("--rp-font-sans: 'Manrope Variable'")
expect(baseCss).toContain('font-family: var(--rp-font-sans)')
expect(packageJson.dependencies['@fontsource-variable/manrope']).toBeDefined()
expect(`${indexCss}\n${baseCss}`).not.toMatch(/fonts\.(?:googleapis|gstatic)\.com/i)
```

Resolve the installed package from the lockfile and assert its license metadata remains `OFL-1.1`. Task 9 performs the browser-level Cyrillic glyph/load assertion, so this test does not pretend that a CSS string alone proves glyph coverage.

- [ ] **Step 2: Run the tests and verify missing modules fail**

Run:

```bash
cd apps/web
npm test -- src/design-system/theme/ThemeProvider.test.tsx
npm test -- src/design-system/styles/typography.test.ts
```

Expected: FAIL because the theme modules and local font dependency do not exist.

- [ ] **Step 3: Implement the theme contract and semantic CSS**

Create `apps/web/src/design-system/theme/theme.ts`:

```ts
export type ThemePreference = 'system' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'
export type DensityPreference = 'comfortable' | 'compact'
export type ResolvedDensity = DensityPreference

export const THEME_STORAGE_KEY = 'robopark-theme'
export const THEME_MEDIA_QUERY = '(prefers-color-scheme: dark)'
export const DENSITY_STORAGE_KEY = 'robopark-density'
export const DENSITY_MEDIA_QUERY = '(max-width: 899px)'

export function readThemePreference(): ThemePreference {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY)
    return stored === 'light' || stored === 'dark' || stored === 'system' ? stored : 'system'
  } catch {
    return 'system'
  }
}

export function resolveTheme(preference: ThemePreference, systemDark: boolean): ResolvedTheme {
  return preference === 'system' ? (systemDark ? 'dark' : 'light') : preference
}

export function applyTheme(theme: ResolvedTheme): void {
  document.documentElement.dataset.theme = theme
  document.documentElement.style.colorScheme = theme
}

export function readDensityPreference(): DensityPreference {
  try {
    return localStorage.getItem(DENSITY_STORAGE_KEY) === 'compact' ? 'compact' : 'comfortable'
  } catch {
    return 'comfortable'
  }
}

export function resolveDensity(preference: DensityPreference, narrowViewport: boolean): ResolvedDensity {
  return narrowViewport ? 'comfortable' : preference
}

export function applyDensity(density: ResolvedDensity): void {
  document.documentElement.dataset.density = density
}

export function applyInitialTheme(): void {
  const preference = readThemePreference()
  applyTheme(resolveTheme(preference, window.matchMedia(THEME_MEDIA_QUERY).matches))
  const density = readDensityPreference()
  applyDensity(resolveDensity(density, window.matchMedia(DENSITY_MEDIA_QUERY).matches))
}
```

Create `ThemeProvider.tsx` with this public context shape:

```tsx
import { createContext, useContext, useEffect, useMemo, useState, type PropsWithChildren } from 'react'
import {
  applyTheme,
  applyDensity,
  DENSITY_MEDIA_QUERY,
  DENSITY_STORAGE_KEY,
  readDensityPreference,
  readThemePreference,
  resolveDensity,
  resolveTheme,
  THEME_MEDIA_QUERY,
  THEME_STORAGE_KEY,
  type ResolvedTheme,
  type ResolvedDensity,
  type DensityPreference,
  type ThemePreference,
} from './theme'

export type { DensityPreference, ThemePreference } from './theme'

type ThemeContextValue = {
  preference: ThemePreference
  resolvedTheme: ResolvedTheme
  setPreference: (preference: ThemePreference) => void
  densityPreference: DensityPreference
  resolvedDensity: ResolvedDensity
  setDensityPreference: (preference: DensityPreference) => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

export function ThemeProvider({ children }: PropsWithChildren) {
  const [preference, setPreferenceState] = useState<ThemePreference>(readThemePreference)
  const [systemDark, setSystemDark] = useState(() => matchMedia(THEME_MEDIA_QUERY).matches)
  const [densityPreference, setDensityPreferenceState] = useState<DensityPreference>(readDensityPreference)
  const [narrowViewport, setNarrowViewport] = useState(() => matchMedia(DENSITY_MEDIA_QUERY).matches)
  const resolvedTheme = resolveTheme(preference, systemDark)
  const resolvedDensity = resolveDensity(densityPreference, narrowViewport)

  useEffect(() => {
    const media = matchMedia(THEME_MEDIA_QUERY)
    const onChange = (event: MediaQueryListEvent) => setSystemDark(event.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])

  useEffect(() => {
    const media = matchMedia(DENSITY_MEDIA_QUERY)
    const onChange = (event: MediaQueryListEvent) => setNarrowViewport(event.matches)
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])

  useEffect(() => applyTheme(resolvedTheme), [resolvedTheme])
  useEffect(() => applyDensity(resolvedDensity), [resolvedDensity])

  const value = useMemo<ThemeContextValue>(() => ({
    preference,
    resolvedTheme,
    densityPreference,
    resolvedDensity,
    setPreference(next) {
      try { localStorage.setItem(THEME_STORAGE_KEY, next) } catch { /* in-memory preference still works */ }
      setPreferenceState(next)
    },
    setDensityPreference(next) {
      try { localStorage.setItem(DENSITY_STORAGE_KEY, next) } catch { /* in-memory preference still works */ }
      setDensityPreferenceState(next)
    },
  }), [densityPreference, preference, resolvedDensity, resolvedTheme])

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext)
  if (!value) throw new Error('useTheme must be used inside ThemeProvider')
  return value
}
```

Start `tokens.css` with the approved semantic pairs:

```css
:root {
  --rp-font-sans: 'Manrope Variable', 'Segoe UI', sans-serif;
  --rp-font-mono: ui-monospace, 'SFMono-Regular', Consolas, monospace;
  --rp-font-size-caption: 0.75rem;
  --rp-font-size-body: 0.875rem;
  --rp-font-size-control: 1rem;
  --rp-font-size-title: 1.25rem;
  --rp-font-size-display: clamp(1.5rem, 3vw, 2rem);
  --rp-line-height-compact: 1.25;
  --rp-line-height-body: 1.5;
  --rp-font-weight-regular: 450;
  --rp-font-weight-medium: 600;
  --rp-font-weight-bold: 700;
  --rp-space-1: 4px;
  --rp-space-2: 8px;
  --rp-space-3: 12px;
  --rp-space-4: 16px;
  --rp-space-6: 24px;
  --rp-space-8: 32px;
  --rp-radius-control: 10px;
  --rp-radius-panel: 16px;
  --rp-motion-fast: 120ms;
  --rp-motion-normal: 180ms;
}

:root[data-density='comfortable'] {
  --rp-control-min-size: 44px;
  --rp-row-min-size: 48px;
  --rp-density-gap: 12px;
  --rp-density-panel-padding: 16px;
}

:root[data-density='compact'] {
  --rp-control-min-size: 44px;
  --rp-row-min-size: 44px;
  --rp-density-gap: 8px;
  --rp-density-panel-padding: 12px;
}

@media (max-width: 899px) {
  :root {
    --rp-control-min-size: 44px;
    --rp-row-min-size: 48px;
    --rp-density-gap: 12px;
    --rp-density-panel-padding: 16px;
  }
}

:root[data-theme='light'] {
  --rp-canvas: #f4f3ef;
  --rp-surface: #ffffff;
  --rp-surface-elevated: #ffffff;
  --rp-surface-sunken: #e9ecef;
  --rp-text: #171a1d;
  --rp-text-muted: #59636d;
  --rp-text-inverse: #ffffff;
  --rp-text-disabled: #6f7982;
  --rp-border: #cbd2d8;
  --rp-divider: #e2e6ea;
  --rp-action: #f5b942;
  --rp-action-on: #171a1d;
  --rp-action-hover: #dfa429;
  --rp-critical: #b42318;
  --rp-critical-on: #ffffff;
  --rp-critical-surface: #fef3f2;
  --rp-warning: #92400e;
  --rp-warning-surface: #fff7e0;
  --rp-success: #166534;
  --rp-success-surface: #ecfdf3;
  --rp-info: #1d4ed8;
  --rp-info-surface: #eff6ff;
  --rp-focus: #005fcc;
  --rp-state-hover: rgb(23 26 29 / 6%);
  --rp-state-selected: #fff1cf;
  --rp-state-disabled: rgb(23 26 29 / 10%);
  --rp-state-pressed: rgb(23 26 29 / 14%);
  --rp-chart-1: #1d4ed8;
  --rp-chart-2: #166534;
  --rp-chart-3: #92400e;
  --rp-chart-4: #6d28d9;
  --rp-overlay: rgb(14 19 24 / 55%);
  --rp-shadow-panel: 0 12px 32px rgb(23 26 29 / 10%);
}

:root[data-theme='dark'] {
  --rp-canvas: #0e1318;
  --rp-surface: #151c22;
  --rp-surface-elevated: #1c252d;
  --rp-surface-sunken: #0a0f13;
  --rp-text: #f5f4ef;
  --rp-text-muted: #aeb7c0;
  --rp-text-inverse: #171a1d;
  --rp-text-disabled: #8e99a3;
  --rp-border: #3b4650;
  --rp-divider: #2b353e;
  --rp-action: #f5b942;
  --rp-action-on: #171a1d;
  --rp-action-hover: #ffd06c;
  --rp-critical: #ff8a80;
  --rp-critical-on: #210503;
  --rp-critical-surface: #3a1515;
  --rp-warning: #ffc66d;
  --rp-warning-surface: #33230f;
  --rp-success: #78d79a;
  --rp-success-surface: #112b1b;
  --rp-info: #86baff;
  --rp-info-surface: #10233b;
  --rp-focus: #8bc8ff;
  --rp-state-hover: rgb(245 244 239 / 8%);
  --rp-state-selected: #3b301d;
  --rp-state-disabled: rgb(245 244 239 / 12%);
  --rp-state-pressed: rgb(245 244 239 / 18%);
  --rp-chart-1: #86baff;
  --rp-chart-2: #78d79a;
  --rp-chart-3: #ffc66d;
  --rp-chart-4: #c4a7ff;
  --rp-overlay: rgb(0 0 0 / 72%);
  --rp-shadow-panel: 0 14px 36px rgb(0 0 0 / 34%);
}
```

Install the local variable font with `npm install @fontsource-variable/manrope`. The lockfile, rather than a floating version in prose, records the reviewed package version. `styles/index.css` first imports `@fontsource-variable/manrope/index.css`, then tokens, base, and layout; Vite must emit the font asset into the application build, so production has no third-party font dependency. `base.css` applies `var(--rp-font-sans)` to the document, sets 14 px minimum body/control text, 16 px form controls through `899px`, applies `var(--rp-control-min-size)`/`var(--rp-row-min-size)` to the relevant primitives, adds a 2 px focus-visible outline, and supplies full reduced-motion fallbacks. `layout.css` defines the four approved media ranges. Do not add a Google Fonts `<link>`, CSS `@import url(...)`, logo font, or copied commercial typeface.

Add this fail-safe bootstrap in `index.html` `<head>` immediately after the viewport meta and before any stylesheet or module script, so a stored explicit theme cannot flash as the default theme while the React bundle loads:

```html
<script>
  (() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    let preference = 'system'
    let density = 'comfortable'
    try {
      const stored = window.localStorage.getItem('robopark-theme')
      if (stored === 'light' || stored === 'dark' || stored === 'system') preference = stored
    } catch {
      preference = 'system'
    }
    try {
      if (window.localStorage.getItem('robopark-density') === 'compact') density = 'compact'
    } catch {
      density = 'comfortable'
    }
    const resolved = preference === 'system' ? (media.matches ? 'dark' : 'light') : preference
    const resolvedDensity = window.matchMedia('(max-width: 899px)').matches ? 'comfortable' : density
    document.documentElement.dataset.theme = resolved
    document.documentElement.dataset.density = resolvedDensity
    document.documentElement.style.colorScheme = resolved
    window.__roboparkThemeBootstrappedAt = performance.now()
  })()
</script>
```

Keep both storage keys, both media queries, and every accepted value identical to `theme.ts`; the inline copy is deliberately dependency-free because a module import would run too late for first paint. The timestamp is non-secret test instrumentation used only to prove prepaint ordering and may be declared in a small `Window` type augmentation. Update `main.tsx` to call `applyInitialTheme()` before render as an idempotent validation, wrap the application in `ThemeProvider`, and import `./design-system/styles/index.css` after `./index.css`. Update the existing AppShell to read theme and density from `useTheme()` so no second persisted appearance state remains. Delete the old `src/theme.ts` after its imports reach zero.

Create `check-contrast.mjs` to parse hex values from both theme blocks in `tokens.css`, compute relative luminance, and fail when any of these foreground/background pairs is below `4.5`: `text/surface`, `text/canvas`, `text-muted/surface`, `action-on/action`, `action-on/action-hover`, `critical-on/critical`, `warning/warning-surface`, `success/success-surface`, and `info/info-surface`. Also require every semantic token named above, including typography and the hover/selected/disabled/pressed layers, in each resolved theme; explicit common `:root` declarations are merged into both light and dark maps before validation. Add `"check:contrast": "node scripts/check-contrast.mjs"` to `package.json`.

- [ ] **Step 4: Verify theme behavior and contrast**

Run:

```bash
cd apps/web
npm test -- src/design-system/theme/ThemeProvider.test.tsx
npm test -- src/design-system/styles/typography.test.ts
npm run check:contrast
npm run build
```

Expected: theme, density, bootstrap-order, and self-hosted-font tests pass; all eighteen theme/pair checks report at least `4.5`; the production bundle contains the local variable font and no Google Fonts URL; and TypeScript/Vite build succeeds. Task 9 then exercises both resolved themes, responsive density, and Cyrillic font loading in a real browser; Plan 05 traces the complete appearance persistence gate.

- [ ] **Step 5: Commit the theme foundation**

```bash
git add apps/web/index.html apps/web/src/design-system apps/web/src/main.tsx apps/web/src/components/AppShell.tsx apps/web/src/theme.ts apps/web/scripts/check-contrast.mjs apps/web/package.json apps/web/package-lock.json
git commit -m "feat(web): add Robopark semantic themes"
```

---

### Task 3: Add the icon, action, status, layout, and form primitives

**Files:**

- Create: `apps/web/src/design-system/icons/Icon.tsx`
- Create: `apps/web/src/design-system/icons/robotGlyphs.tsx`
- Create: `apps/web/src/design-system/icons/Icon.test.tsx`
- Create: `apps/web/src/design-system/actions/Button.tsx`
- Create: `apps/web/src/design-system/actions/Button.css`
- Create: `apps/web/src/design-system/actions/Button.test.tsx`
- Create: `apps/web/src/design-system/status/StatusBadge.tsx`
- Create: `apps/web/src/design-system/status/StatusBadge.css`
- Create: `apps/web/src/design-system/status/StatusBadge.test.tsx`
- Create: `apps/web/src/design-system/layout/PageLayout.tsx`
- Create: `apps/web/src/design-system/layout/PageLayout.css`
- Create: `apps/web/src/design-system/layout/PageLayout.test.tsx`
- Create: `apps/web/src/design-system/forms/FormField.tsx`
- Create: `apps/web/src/design-system/forms/FormField.css`
- Create: `apps/web/src/design-system/forms/FormField.test.tsx`
- Modify: `apps/web/package.json`
- Modify: `apps/web/package-lock.json`

**Interfaces:**

- Produces the `IconName`, `Icon`, `Button`, `IconButton`, `StatusTone`, `StatusBadge`, `PageLayout`, `Panel`, and `FormField` contracts listed in Plan Boundary.
- `IconButton` always requires a visible-to-assistive-technology `label`.
- General UI symbols use one Lucide family; the frozen `robot` and `robot-check` names render original code-native Robopark SVG glyphs. The latter includes a small sensor/check motif, uses `currentColor`, and copies no commercial robot silhouette.

- [ ] **Step 1: Install the one icon family and write primitive tests**

Run:

```bash
cd apps/web
npm install lucide-react
npm install --save-dev @testing-library/user-event
```

Create tests that assert:

```tsx
render(<Icon name="robot-check" data-testid="icon" />)
expect(screen.getByTestId('icon')).toHaveAttribute('aria-hidden', 'true')
expect(screen.getByTestId('icon')).toHaveAttribute('data-rp-glyph', 'robot-check')

render(<Icon name="robot" data-testid="robot-icon" />)
expect(screen.getByTestId('robot-icon')).toHaveAttribute('data-rp-glyph', 'robot')
expect(screen.getByTestId('robot-icon')).toHaveAttribute('stroke', 'currentColor')

render(<Button busy>Сохранить</Button>)
expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled()
expect(screen.getByRole('button', { name: 'Сохранить' })).toHaveAttribute('aria-busy', 'true')

render(<IconButton icon="refresh" label="Обновить данные" />)
expect(screen.getByRole('button', { name: 'Обновить данные' })).toBeVisible()

render(<StatusBadge tone="critical">Критично</StatusBadge>)
expect(screen.getByText('Критично')).toHaveAttribute('data-tone', 'critical')

render(
  <PageLayout
    actions={<button type="button">Обновить</button>}
    description="Текущая смена"
    eyebrow="Парк: Север"
    title="Обзор"
  >
    <p>Содержимое</p>
  </PageLayout>,
)
expect(screen.getByRole('heading', { level: 1, name: 'Обзор' })).toBeVisible()
expect(screen.getByText('Парк: Север')).toBeVisible()
expect(screen.getByRole('button', { name: 'Обновить' })).toBeVisible()

render(<Panel actions={<button type="button">Ещё</button>} description="Детали" title="Риск"><p>Статус</p></Panel>)
expect(screen.getByRole('region', { name: 'Риск' })).toHaveTextContent('Детали')

render(<FormField error="Обязательное поле" hint="До 100 знаков" id="title" label="Название" required><input /></FormField>)
const field = screen.getByLabelText('Название')
expect(field).toHaveAttribute('id', 'title')
expect(field).toHaveAttribute('aria-invalid', 'true')
expect(field).toHaveAttribute('aria-describedby', 'title-hint title-error')
```

- [ ] **Step 2: Run the primitive tests and verify they fail**

Run:

```bash
cd apps/web
npm test -- \
  src/design-system/icons/Icon.test.tsx \
  src/design-system/actions/Button.test.tsx \
  src/design-system/status/StatusBadge.test.tsx \
  src/design-system/layout/PageLayout.test.tsx \
  src/design-system/forms/FormField.test.tsx
```

Expected: FAIL because the components do not exist.

- [ ] **Step 3: Implement the public primitive contracts**

`Icon.tsx` maps the frozen names to Lucide components:

```tsx
import type { ComponentType } from 'react'
import {
  ArrowLeft, ArrowRight, BarChart3, Camera, CheckCircle2,
  ChevronDown, CircleAlert, ClipboardList, Clock3, Download,
  Ellipsis, ExternalLink, Filter, Gauge, Info, KeyRound, LogOut,
  Menu, MessageSquareText, Paperclip, PlugZap, RefreshCw, ScanLine, Search,
  Send, ServerCog, Settings, ShieldCheck, SunMoon, TriangleAlert, UserRound,
  Users, Warehouse, WifiOff, X, type LucideProps,
} from 'lucide-react'
import { RobotCheckGlyph, RobotGlyph } from './robotGlyphs'

export type IconName =
  | 'overview' | 'work' | 'robot' | 'robot-check' | 'reports' | 'analytics'
  | 'parks' | 'users' | 'roles' | 'integration' | 'safety' | 'system'
  | 'search' | 'scan' | 'more' | 'theme' | 'logout' | 'back' | 'forward'
  | 'close' | 'refresh' | 'warning' | 'critical' | 'success' | 'info'
  | 'offline' | 'attachment' | 'camera' | 'download' | 'send' | 'filter'
  | 'clock' | 'assignee' | 'menu' | 'chevron-down' | 'settings' | 'external-link'

const ICONS = {
  overview: Gauge, work: ClipboardList, robot: RobotGlyph, 'robot-check': RobotCheckGlyph,
  reports: MessageSquareText, analytics: BarChart3, parks: Warehouse, users: Users,
  roles: KeyRound, integration: PlugZap, safety: ShieldCheck, system: ServerCog,
  search: Search, scan: ScanLine, more: Ellipsis, theme: SunMoon, logout: LogOut,
  back: ArrowLeft, forward: ArrowRight, close: X, refresh: RefreshCw,
  warning: TriangleAlert, critical: CircleAlert, success: CheckCircle2, info: Info,
  offline: WifiOff, attachment: Paperclip, camera: Camera, download: Download,
  send: Send, filter: Filter, clock: Clock3, assignee: UserRound, menu: Menu,
  'chevron-down': ChevronDown, settings: Settings, 'external-link': ExternalLink,
} satisfies Record<IconName, ComponentType<LucideProps>>

export function Icon({ name, ...props }: { name: IconName } & LucideProps) {
  const Component = ICONS[name]
  return <Component {...props} aria-hidden="true" focusable="false" />
}
```

Implement `RobotGlyph` and `RobotCheckGlyph` as small inline `<svg viewBox="0 0 24 24">` components accepting `LucideProps`: rounded delivery-body/wheel strokes for `robot`, and the same abstract body plus three radiating sensor arcs and a check stroke for `robot-check`. Set `fill="none"`, `stroke="currentColor"`, `strokeLinecap="round"`, `strokeLinejoin="round"`, and `data-rp-glyph` on the root; never embed text, raster data, a logo, a copied chassis outline, or raw theme color. Keep every path simple enough to remain legible at 16 px. `Icon.test.tsx` also reads `robotGlyphs.tsx` and asserts it contains no `yandex`, external URL, `<image>`, or base64 payload. Remove unused Lucide imports during implementation so Oxlint passes.

`Button.tsx` uses native button props and a controlled busy state:

```tsx
import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { Icon, type IconName } from '../icons/Icon'
import './Button.css'

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'comfortable' | 'compact'
  leadingIcon?: IconName
  busy?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size = 'comfortable', leadingIcon, busy = false,
    disabled, className = '', children, ...props }, ref,
) {
  return (
    <button
      {...props}
      aria-busy={busy || undefined}
      className={`rp-button rp-button--${variant} rp-button--${size} ${className}`.trim()}
      disabled={disabled || busy}
      ref={ref}
    >
      {leadingIcon ? <Icon name={leadingIcon} size={18} /> : null}
      <span>{children}</span>
    </button>
  )
})

export function IconButton({ label, icon, ...props }:
  Omit<ButtonProps, 'children' | 'leadingIcon'> & { label: string; icon: IconName }) {
  return <Button {...props} aria-label={label} className="rp-icon-button"><Icon name={icon} /></Button>
}
```

Implement the remaining primitives against these exact public contracts:

```tsx
export type StatusTone = 'neutral' | 'info' | 'success' | 'warning' | 'critical'
export type StatusBadgeProps = {
  tone: StatusTone
  icon?: IconName
  children: ReactNode
  className?: string
}
export function StatusBadge(props: StatusBadgeProps): ReactElement

export type PageLayoutProps = {
  title: ReactNode
  description?: ReactNode
  eyebrow?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
}
export function PageLayout(props: PageLayoutProps): ReactElement

export type PanelProps = {
  title?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
}
export function Panel(props: PanelProps): ReactElement

export type FormFieldProps = {
  id: string
  label: ReactNode
  hint?: ReactNode
  error?: ReactNode
  required?: boolean
  children: ReactElement
  className?: string
}
export function FormField(props: FormFieldProps): ReactElement
```

`StatusBadge` sets `data-tone`, uses the supplied icon or the exact defaults `neutral→info`, `info→info`, `success→success`, `warning→warning`, `critical→critical`, and always retains visible text. `PageLayout` owns one page header and content wrapper; its title is an `h1`, eyebrow precedes it, description follows it, and actions remain in the same header. `Panel` is a `section`; when `title` exists, generate a React `useId()` heading ID and set `aria-labelledby` on the section, otherwise do not invent an accessible name. `FormField` requires exactly one form element, clones it with `id`, `required`, `aria-invalid={Boolean(error)}`, and `aria-describedby` in stable `id-hint id-error` order while preserving any caller-provided described-by tokens. Hint uses `${id}-hint`, error uses `${id}-error` and `role="alert"`.

CSS must use only `--rp-*` tokens, provide 44 px comfortable controls, and never place white text on `--rp-action`.

- [ ] **Step 4: Verify primitives, lint, and build**

Run:

```bash
cd apps/web
npm test -- \
  src/design-system/icons/Icon.test.tsx \
  src/design-system/actions/Button.test.tsx \
  src/design-system/status/StatusBadge.test.tsx \
  src/design-system/layout/PageLayout.test.tsx \
  src/design-system/forms/FormField.test.tsx
npm run lint
npm run build
```

Expected: tests, Oxlint, and build pass; no Unicode navigation glyph is introduced in new files.

- [ ] **Step 5: Commit the primitive layer**

```bash
git add apps/web/package.json apps/web/package-lock.json apps/web/src/design-system
git commit -m "feat(web): add operational design primitives"
```

---

### Task 4: Add accessible asynchronous states

**Files:**

- Create: `apps/web/src/design-system/feedback/AsyncState.tsx`
- Create: `apps/web/src/design-system/feedback/AsyncState.css`
- Create: `apps/web/src/design-system/feedback/AsyncState.test.tsx`

**Interfaces:**

- Produces: `LoadingState`, `EmptyState`, `ErrorState`, `Freshness`, and `StaleBadge` with the frozen props from the inter-plan contract.
- Consumes: `Button`, `Icon`, `StatusBadge`.

- [ ] **Step 1: Write semantic-state tests**

```tsx
render(<LoadingState label="Загружаем парк" variant="panel" />)
expect(screen.getByRole('status', { name: 'Загружаем парк' })).toHaveAttribute('aria-busy', 'true')

render(<ErrorState title="Не удалось загрузить" description="Проверьте сеть" onRetry={retry} />)
expect(screen.getByRole('alert')).toHaveTextContent('Проверьте сеть')
await user.click(screen.getByRole('button', { name: 'Повторить' }))
expect(retry).toHaveBeenCalledOnce()

render(<StaleBadge state="stale" updatedAt="2026-09-02T08:00:00Z" />)
expect(screen.getByText(/Данные устарели/)).toBeVisible()
```

- [ ] **Step 2: Verify the tests fail before implementation**

Run `npm test -- src/design-system/feedback/AsyncState.test.tsx` from `apps/web`.

Expected: FAIL because `AsyncState.tsx` does not exist.

- [ ] **Step 3: Implement explicit loading, empty, error, and freshness output**

Use this public type surface:

```tsx
export type Freshness = 'live' | 'fresh' | 'stale' | 'offline'

export function LoadingState({ label, variant = 'panel' }: {
  label: string
  variant?: 'inline' | 'panel' | 'page'
})

export function EmptyState({ title, description, icon, action }: {
  title: string
  description?: string
  icon?: IconName
  action?: ReactNode
})

export function ErrorState({ title, description, onRetry, retryLabel = 'Повторить', requestId }: {
  title: string
  description: string
  onRetry?: () => void
  retryLabel?: string
  requestId?: string
})

export function StaleBadge({ state, updatedAt, label }: {
  state: Freshness
  updatedAt?: string | null
  label?: string
})
```

`LoadingState` owns `role="status"`, an accessible name, and `aria-busy`. `ErrorState` owns `role="alert"`. `StaleBadge` renders an icon and Russian text for every state; it uses `Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' })` for a valid timestamp and omits the time when invalid.

- [ ] **Step 4: Run focused and full web tests**

Run:

```bash
cd apps/web
npm test -- src/design-system/feedback/AsyncState.test.tsx
npm test
```

Expected: all Vitest tests pass.

- [ ] **Step 5: Commit async states**

```bash
git add apps/web/src/design-system/feedback
git commit -m "feat(web): add accessible async states"
```

---

### Task 5: Add accessible dialogs, sheets, confirmations, and tabs

**Files:**

- Create: `apps/web/src/design-system/overlays/Dialog.tsx`
- Create: `apps/web/src/design-system/overlays/BottomSheet.tsx`
- Create: `apps/web/src/design-system/overlays/ConfirmDialog.tsx`
- Create: `apps/web/src/design-system/overlays/overlays.css`
- Create: `apps/web/src/design-system/overlays/Dialog.test.tsx`
- Create: `apps/web/src/design-system/overlays/ConfirmDialog.test.tsx`
- Create: `apps/web/src/design-system/navigation/Tabs.tsx`
- Create: `apps/web/src/design-system/navigation/Tabs.css`
- Create: `apps/web/src/design-system/navigation/Tabs.test.tsx`

**Interfaces:**

- `DialogProps`: controlled open state, title/description, body/footer, optional initial focus, close label, and dismissibility.
- `BottomSheetProps`: identical public fields to `DialogProps`, presentation changes at compact widths.
- `ConfirmDialogProps`: controlled mutation state and optional typed confirmation phrase; it does not auto-close.
- Tabs produce linked `tab` and `tabpanel` IDs and move DOM focus with arrow/Home/End keys.

- [ ] **Step 1: Write focus-management and confirmation tests**

The dialog tests must assert initial focus, Tab wrapping, Escape, inert background, body scroll lock, and focus restoration:

```tsx
const opener = document.createElement('button')
opener.textContent = 'Открыть'
document.body.append(opener)
opener.focus()
const onOpenChange = vi.fn()
render(
  <Dialog open onOpenChange={onOpenChange} title="Запросить парк">
    <button type="button">Первое действие</button>
    <button type="button">Последнее действие</button>
  </Dialog>,
)
expect(screen.getByRole('button', { name: 'Первое действие' })).toHaveFocus()
fireEvent.keyDown(document, { key: 'Escape' })
expect(onOpenChange).toHaveBeenCalledWith(false)
```

The confirmation test must assert that entering any value except the exact phrase keeps the danger button disabled, `pending` disables both actions, and `error` is a live alert.

Add two regressions:

```tsx
it('focuses the dialog panel when a mandatory dialog has no controls', () => {
  render(<Dialog dismissible={false} onOpenChange={vi.fn()} open role="alertdialog" title="Техработы"><p>Ожидайте</p></Dialog>)
  expect(screen.getByRole('alertdialog')).toHaveFocus()
  fireEvent.keyDown(document, { key: 'Tab' })
  expect(screen.getByRole('alertdialog')).toHaveFocus()
})

it('clears a typed phrase after close and when the protected entity changes', async () => {
  const { rerender } = renderConfirm({ open: true, confirmationPhrase: 'DELETE-A' })
  await userEvent.type(screen.getByRole('textbox'), 'DELETE-A')
  expect(screen.getByRole('button', { name: 'Удалить' })).toBeEnabled()
  rerender(renderConfirmElement({ open: false, confirmationPhrase: 'DELETE-A' }))
  rerender(renderConfirmElement({ open: true, confirmationPhrase: 'DELETE-B' }))
  expect(screen.getByRole('textbox')).toHaveValue('')
  expect(screen.getByRole('button', { name: 'Удалить' })).toBeDisabled()
})
```

Define the tiny helpers locally so the same component instance is rerendered; do not unmount between assertions, because the regression is retained state.

The tabs test must call `.focus()` on the active tab, press ArrowRight, then assert both `onChange(nextId)` and focus on the next tab. It must also assert `aria-controls`/`aria-labelledby` linkage.

- [ ] **Step 2: Run focused tests and verify missing components fail**

Run:

```bash
cd apps/web
npm test -- src/design-system/overlays/Dialog.test.tsx src/design-system/overlays/ConfirmDialog.test.tsx src/design-system/navigation/Tabs.test.tsx
```

Expected: FAIL because the new overlay and tab modules do not exist.

- [ ] **Step 3: Implement the overlay focus contract**

Use the exact public props:

```tsx
export type DialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  children: ReactNode
  footer?: ReactNode
  initialFocusRef?: RefObject<HTMLElement | null>
  closeLabel?: string
  dismissible?: boolean
  role?: 'dialog' | 'alertdialog'
}

export type ConfirmDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  confirmLabel: string
  cancelLabel?: string
  tone?: 'default' | 'danger'
  confirmationPhrase?: string
  pending?: boolean
  error?: string | null
  onConfirm: () => void | Promise<void>
}
```

Build the dialog element in a local `dialog` constant and return `createPortal(dialog, document.body)`. Generate title/description IDs with `useId`, default `role` to `dialog`, set `aria-modal="true"`, and place the dialog outside `#root`. While open:

1. save `document.activeElement`;
2. set `document.querySelector('#root')?.setAttribute('inert', '')`;
3. set `document.body.style.overflow = 'hidden'`;
4. give the dialog panel `tabIndex={-1}` and a ref, then focus `initialFocusRef.current`, the first focusable descendant, or the panel itself in that order;
5. intercept Tab/Shift+Tab to wrap between focusable descendants; when there are none, prevent default and keep focus on the panel;
6. close on Escape only when `dismissible !== false`;
7. restore inert, scroll style, and prior focus during cleanup.

`BottomSheet` delegates behavior to `Dialog` and adds `rp-bottom-sheet` presentation. `ConfirmDialog` keeps phrase input state local, resets it whenever `open` becomes false or `confirmationPhrase` changes, calls `onConfirm`, and leaves closing/result state to the parent. A previously entered phrase can never enable a later confirmation for another entity.

Implement the new tabs with this exact linkage surface:

```tsx
export type TabItem = { id: string; label: string; count?: number }

export function Tabs({ items, value, onChange, ariaLabel, panelIdFor }: {
  items: readonly TabItem[]
  value: string
  onChange: (id: string) => void
  ariaLabel: string
  panelIdFor: (id: string) => string
})

export function TabPanel({ id, labelledBy, active, children }: {
  id: string
  labelledBy: string
  active: boolean
  children: ReactNode
})
```

Each tab ID is `tab-${item.id}`. ArrowRight/ArrowLeft wrap, Home selects/focuses the first item, and End selects/focuses the last.

- [ ] **Step 4: Verify overlays and tabs**

Run:

```bash
cd apps/web
npm test -- src/design-system/overlays src/design-system/navigation
npm run lint
npm run build
```

Expected: focused tests, Oxlint, and build pass with no React act warnings.

- [ ] **Step 5: Commit interaction primitives**

```bash
git add apps/web/src/design-system/overlays apps/web/src/design-system/navigation
git commit -m "feat(web): add accessible overlays and tabs"
```

---

### Task 6: Centralize routes, access policy, and role-aware navigation

**Files:**

- Create: `apps/web/src/app/routing/routeManifest.ts`
- Create: `apps/web/src/app/routing/accessPolicy.ts`
- Create: `apps/web/src/app/routing/routeManifest.test.ts`
- Create: `apps/web/src/app/routing/accessPolicy.test.ts`
- Modify: `apps/web/src/i18n/ru.ts`

**Interfaces:**

- Produces the frozen `AppRouteId`, `UserRole`, `NavGroup`, `NavSurface`, `AccessPrerequisite`, `RouteNav`, `RouteManifestItem`, `NavigationItem`, `isSystemUserRole`, and pure policy functions.
- Consumes: `User` from `apps/web/src/api.ts` and `IconName` from the design system.

- [ ] **Step 1: Write the access matrix before the manifest**

Create table-driven tests for these exact cases:

```ts
import type { User } from '../../api'

function user(overrides: Partial<User>): User {
  return {
    id: 1,
    username: 'matrix-user',
    role: 'operator',
    access_status: 'approved',
    permissions: [],
    parks: [],
    ...overrides,
  }
}

const cases = [
  { role: 'mechanic', status: 'approved', parks: [], permissions: ['nav.dashboard'], expected: '/mechanic/no-park' },
  { role: 'operator', status: 'pending', parks: [], permissions: ['nav.dashboard'], expected: '/access/pending' },
  { role: 'operator', status: 'rejected', parks: [], permissions: ['nav.dashboard'], expected: '/access/rejected' },
  { role: 'driver', status: 'approved', parks: [], permissions: ['nav.emergency'], expected: '/emergency' },
  { role: 'admin', status: 'approved', parks: [], permissions: ['nav.admin'], expected: '/admin' },
  { role: 'royal', status: 'approved', parks: [], permissions: ['nav.dashboard'], expected: '/overview' },
] as const

it.each(cases)('lands $role/$status on $expected', ({ role, status, parks, permissions, expected }) => {
  expect(landingPathForUser(user({ role, access_status: status, parks, permissions }))).toBe(expected)
})
```

Add an explicit route-policy matrix; do not derive the expected permission/prerequisite values from `ROUTE_MANIFEST`, because the test must catch a malformed manifest:

```ts
const protectedRoutes = [
  { id: 'overview', permission: 'nav.dashboard', operatorOnly: false, mechanicPark: true },
  { id: 'operator-parks', permission: null, operatorOnly: true, mechanicPark: false },
  { id: 'work', permission: 'nav.tasks', operatorOnly: false, mechanicPark: true },
  { id: 'robots', permission: 'nav.robot_search', operatorOnly: false, mechanicPark: true },
  { id: 'robot-check', permission: 'nav.emergency', operatorOnly: false, mechanicPark: true },
  { id: 'analytics', permission: 'nav.analytics', operatorOnly: false, mechanicPark: true },
  { id: 'reports', permission: 'nav.reports', operatorOnly: false, mechanicPark: true },
  { id: 'admin', permission: 'nav.admin', operatorOnly: false, mechanicPark: true },
  { id: 'admin-tracker', permission: 'nav.admin.tracker', operatorOnly: false, mechanicPark: true },
  { id: 'admin-robot-check', permission: 'nav.admin.emergency', operatorOnly: false, mechanicPark: true },
] as const
const matrixRoles = ['mechanic', 'operator', 'driver', 'admin', 'royal', 'field_lead'] as const
const matrixStatuses = ['approved', 'pending', 'rejected'] as const

const accessCases = protectedRoutes.flatMap((route) =>
  matrixRoles.flatMap((role) =>
    matrixStatuses.flatMap((status) =>
      [true, false].map((permissionPresent) => ({ route, role, status, permissionPresent })),
    ),
  ),
)

it.each(accessCases)(
  '$route.id role=$role status=$status permission=$permissionPresent',
  ({ route, role, status, permissionPresent }) => {
    const permissions = route.permission && permissionPresent ? [route.permission] : []
    const expected =
      status === 'approved' &&
      (!route.operatorOnly || role === 'operator') &&
      (!route.permission || permissionPresent)
    expect(canAccessRoute(user({ role, access_status: status, permissions, parks: [north] }), route.id))
      .toBe(expected)
  },
)

it.each(protectedRoutes)('denies $id while password change is required', ({ id, permission }) => {
  expect(canAccessRoute(user({
    role: 'operator',
    access_status: 'approved',
    must_change_password: true,
    permissions: permission ? [permission] : [],
    parks: [north],
  }), id)).toBe(false)
})

it.each(protectedRoutes)('applies mechanic park prerequisite to $id', ({ id, permission, mechanicPark }) => {
  expect(canAccessRoute(user({
    role: 'mechanic',
    access_status: 'approved',
    permissions: permission ? [permission] : [],
    parks: [],
  }), id)).toBe(!mechanicPark && id !== 'operator-parks')
})

it('falls back to permission-driven navigation for a custom role slug', () => {
  const custom = user({
    role: 'field_lead',
    access_status: 'approved',
    permissions: ['nav.dashboard', 'nav.tasks'],
    parks: [north],
  })
  expect(landingPathForUser(custom)).toBe('/overview')
  expect(navigationForUser(custom, 'mobile').map((item) => item.id).slice(0, 2))
    .toEqual(['overview', 'work'])
})
```

Define `north` as a complete synthetic `Park` at the top of the test. In `routeManifest.test.ts`, assert the `overview` record is exactly `{ path: '/overview', legacyPaths: ['/dashboard', '/operator'] }` for those fields and that no other canonical/legacy path duplicates either alias. Also assert all public/standalone IDs (`home`, `login`, `register`, `change-password`, `no-cabinet`, `access-pending`, `access-rejected`, `mechanic-no-park`, `not-found`) appear exactly once and require no navigation permission. Assert desktop ordering is stable and that the mechanic, operator, driver, admin, royal, and custom-role mobile arrays have at most four primary items before «Ещё».

- [ ] **Step 2: Run the policy tests and verify missing modules fail**

Run:

```bash
cd apps/web
npm test -- src/app/routing/routeManifest.test.ts src/app/routing/accessPolicy.test.ts
```

Expected: FAIL because manifest and policy modules do not exist.

- [ ] **Step 3: Implement the frozen manifest types and initial compatibility entries**

Use these exact types:

```ts
export const SYSTEM_USER_ROLES = ['royal', 'admin', 'operator', 'mechanic', 'driver'] as const
export type UserRole = typeof SYSTEM_USER_ROLES[number]

export function isSystemUserRole(role: string): role is UserRole {
  return (SYSTEM_USER_ROLES as readonly string[]).includes(role)
}

export type AppRouteId =
  | 'home' | 'login' | 'register' | 'change-password' | 'no-cabinet'
  | 'access-pending' | 'access-rejected' | 'mechanic-no-park'
  | 'overview' | 'operator-parks' | 'work' | 'robots' | 'robot-check'
  | 'analytics' | 'reports' | 'admin' | 'admin-tracker'
  | 'admin-robot-check' | 'not-found'
export type NavGroup = 'operations' | 'collaboration' | 'insights' | 'administration'
export type NavSurface = 'desktop' | 'mobile'
export type AccessPrerequisite = 'password-changed' | 'approved' | 'mechanic-has-park'
export type RouteNav = {
  group: NavGroup
  desktopOrder: number
  mobilePriority?: Partial<Record<UserRole, number>>
}
export type RouteManifestItem = {
  id: AppRouteId
  path: string
  legacyPaths?: readonly string[]
  label: string
  icon: IconName
  permission?: string
  prerequisites?: readonly AccessPrerequisite[]
  surface: 'public' | 'standalone' | 'shell'
  nav?: RouteNav
}
export type NavigationItem = Pick<RouteManifestItem, 'id' | 'path' | 'label' | 'icon'> & {
  group: NavGroup
  priority: number
}
```

Initial shell entries map `/overview` with exact legacy aliases `/dashboard` and `/operator`, `/work` with legacy `/tasks`, and `/robots` with legacy `/robots/search` to their existing page components. `/operator` means the operator landing screen, not the retained more-specific `/operator/parks` compatibility item. The existing `/emergency` path keeps ID `robot-check` and receives the visible label «Проверка робота» until Plan 02 adds `/robots/:vin/check`. Keep existing permissions unchanged.

Implement:

```ts
export type AccessUser = Pick<User,
  'role' | 'access_status' | 'permissions' | 'parks' | 'must_change_password'>

export function canAccessRoute(user: AccessUser, routeId: AppRouteId): boolean
export function landingPathForUser(user: AccessUser): string
export function navigationForUser(user: AccessUser, surface: NavSurface): NavigationItem[]
```

`canAccessRoute` checks the route permission and prerequisites. `landingPathForUser` checks password change, pending/rejected, mechanic park assignment, then role-preferred accessible routes. `navigationForUser` filters manifest entries by access and `nav`, sorts desktop by `desktopOrder`, and sorts mobile by the current role's `mobilePriority`; entries without a mobile priority follow the primary four and remain available through «Ещё».

`User.role` and `AccessUser.role` remain `string`: Phase 4 can assign custom role slugs. Never cast a raw API role to `UserRole`. In `navigationForUser`, read `mobilePriority[user.role]` only after `isSystemUserRole(user.role)`; a custom role receives `priority = 1000 + desktopOrder`, so its permission-filtered desktop order is also its safe mobile fallback. In `landingPathForUser`, use the fixed role-preference table only for a guarded system role; for a custom role, select the first accessible `nav` record by `desktopOrder`. In both branches exclude parameterized paths containing `:` from landing candidates. `operator-parks` remains the one explicit Foundation role restriction (`user.role === 'operator'`) until Plan 04 converts it to a redirect.

Do not integrate these pure modules into the current App/AppShell yet. Keeping the old `routes.ts`, `nav.ts`, and `nav-permissions.ts` unchanged makes Task 6 independently buildable; Task 8 switches all three consumers in one commit.

- [ ] **Step 4: Verify access policy and all existing web tests**

Run:

```bash
cd apps/web
npm test -- src/app/routing
npm test
npm run check-nav
npm run build
```

Expected: route/access tests and the existing suite pass; build resolves no deleted nav import.

- [ ] **Step 5: Commit manifest and policy**

```bash
git add apps/web/src/app/routing apps/web/src/i18n/ru.ts
git commit -m "refactor(web): centralize routes and access policy"
```

---

### Task 7: Make park scope explicit and URL-backed

**Files:**

- Create: `apps/web/src/app/park/parkScope.ts`
- Create: `apps/web/src/app/park/ParkScopeProvider.tsx`
- Create: `apps/web/src/app/park/ParkScopeProvider.test.tsx`
- Modify: `apps/web/src/ParkProvider.tsx`
- Modify: `apps/web/src/park-context.ts`

**Interfaces:**

- Produces: `PARK_QUERY_KEY`, `hasFleetParkScope`, `ParkScopeValue`, `ParkScopeProvider`, and `useParkScope`.
- Consumes: authenticated `User`, `Park`, React Router search params, and `api.parks()` for admin/royal or any custom role explicitly granted `parks.manage`.
- URL key: exactly `park`; its value is a decimal park ID.

- [ ] **Step 1: Write URL, storage, and locked-mechanic tests**

Use a MemoryRouter and synthetic auth context. Define the complete local harness at the top of the test:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { vi } from 'vitest'
import type { Park, User } from '../../api'
import { AuthContext } from '../../auth-context'
import { ParkScopeProvider } from './ParkScopeProvider'
import { useParkScope } from './parkScope'

function park(id: number): Park {
  return { id, name: `Парк ${id}`, tag: `park-${id}`, is_active: true }
}

function scopeUser(role: 'operator' | 'mechanic', parks: Park[]): User {
  return {
    id: 1,
    username: `${role}-scope`,
    role,
    access_status: 'approved',
    permissions: ['nav.dashboard'],
    parks,
  }
}

function Probe() {
  const scope = useParkScope()
  const location = useLocation()
  return (
    <>
      <output data-testid="park-id">{scope.parkId ?? 'none'}</output>
      <output data-testid="location">{location.search}</output>
      <button type="button" onClick={() => scope.setParkId(9)}>Выбрать парк 9</button>
    </>
  )
}

function renderScope(path: string, currentUser: User) {
  const actor = userEvent.setup()
  render(
    <MemoryRouter initialEntries={[path]}>
      <AuthContext.Provider value={{
        user: currentUser,
        loading: false,
        login: vi.fn(),
        refreshUser: vi.fn(),
        logout: vi.fn(),
      }}>
        <ParkScopeProvider><Probe /></ParkScopeProvider>
      </AuthContext.Provider>
    </MemoryRouter>,
  )
  return actor
}
```

Then prove these cases:

```tsx
it('prefers a valid park from the URL over session storage', async () => {
  sessionStorage.setItem('robopark-park-id', '7')
  renderScope('/overview?park=9', scopeUser('operator', [park(7), park(9)]))
  expect(await screen.findByTestId('park-id')).toHaveTextContent('9')
})

it('removes an unknown park and falls back to the first assigned park', async () => {
  renderScope('/overview?park=404', scopeUser('operator', [park(7), park(9)]))
  expect(await screen.findByTestId('park-id')).toHaveTextContent('7')
  expect(screen.getByTestId('location')).toHaveTextContent('?park=7')
})

it('does not let a single-park mechanic change scope', async () => {
  const actor = renderScope('/overview?park=7', scopeUser('mechanic', [park(7)]))
  await actor.click(screen.getByRole('button', { name: 'Выбрать парк 9' }))
  expect(screen.getByTestId('park-id')).toHaveTextContent('7')
})
```

Add a custom-role capability case. Mock `api.parks()` to return `[park(7), park(9)]`, render a complete approved `User` whose raw role is `field_lead`, whose assigned `parks` is empty, and whose permissions contain `parks.manage`; assert the provider loads the fleet list, accepts `setParkId(9)`, writes `?park=9`, and calls `api.parks()` again on `refreshParks()`. A second custom user without `parks.manage` must never call `api.parks()`, must use only its assigned parks, and must reject an unassigned ID.

- [ ] **Step 2: Run the scope tests and verify the provider is missing**

Run:

```bash
cd apps/web
npm test -- src/app/park/ParkScopeProvider.test.tsx
```

Expected: FAIL because `ParkScopeProvider` does not exist.

- [ ] **Step 3: Implement deterministic park precedence and URL updates**

Use this exact public contract in `parkScope.ts`:

```ts
import { createContext, useContext } from 'react'
import type { Park, User } from '../../api'

export const PARK_QUERY_KEY = 'park'
export const PARK_STORAGE_KEY = 'robopark-park-id'

export type ParkScopeUser = Pick<User, 'role' | 'permissions'>

export function hasFleetParkScope(user: ParkScopeUser): boolean {
  return user.role === 'admin'
    || user.role === 'royal'
    || (user.permissions ?? []).includes('parks.manage')
}

export type ParkScopeValue = {
  parkId: number | null
  selectedPark: Park | null
  parks: Park[]
  loading: boolean
  locked: boolean
  setParkId: (id: number, options?: { replace?: boolean }) => void
  refreshParks: () => Promise<void>
}

export const ParkScopeContext = createContext<ParkScopeValue | null>(null)

export function useParkScope(): ParkScopeValue {
  const value = useContext(ParkScopeContext)
  if (!value) throw new Error('useParkScope must be used inside ParkScopeProvider')
  return value
}

export function validParkId(parks: readonly Park[], raw: string | null): number | null {
  if (!raw || !/^\d+$/.test(raw)) return null
  const id = Number(raw)
  return parks.some((item) => item.id === id) ? id : null
}
```

`ParkScopeProvider` loads active `api.parks()` whenever `hasFleetParkScope(user)` is true and otherwise uses only assigned parks from the authenticated user. Resolve precedence as URL → valid session storage → first park → null. When resolution changes, write both the query parameter and session storage with `replace: true`. `setParkId` rejects IDs outside `parks`, rejects every change when a mechanic has at most one park, and defaults `options.replace` to `false` for a deliberate user selection. `refreshParks()` refetches `api.parks()` for fleet-scope actors; for everyone else it awaits `AuthContext.refreshUser()` and derives only the returned user's assigned parks. Never branch on a custom slug or treat `users.manage` as fleet data access.

Expose compatibility without duplicating state:

```tsx
// apps/web/src/ParkProvider.tsx
export { ParkScopeProvider as ParkProvider } from './app/park/ParkScopeProvider'

// apps/web/src/park-context.ts
export { ParkScopeContext as ParkContext, useParkScope as useParkContext } from './app/park/parkScope'
```

- [ ] **Step 4: Verify park scope and existing consumers**

Run:

```bash
cd apps/web
npm test -- src/app/park/ParkScopeProvider.test.tsx src/ParkProvider.test.tsx
npm run build
npm test
```

Expected: new and compatibility tests pass, custom `parks.manage` actors can load/refresh/select fleet parks, actors without it remain assigned-park scoped, build succeeds, and no consumer has an independent selected-park state.

- [ ] **Step 5: Commit URL-backed park scope**

```bash
git add apps/web/src/app/park apps/web/src/ParkProvider.tsx apps/web/src/park-context.ts apps/web/src/ParkProvider.test.tsx
git commit -m "feat(web): persist park scope in the URL"
```

---

### Task 8: Replace the application router and responsive shell

**Files:**

- Create: `apps/web/src/app/routing/RouteGate.tsx`
- Create: `apps/web/src/app/routing/AppRouter.tsx`
- Create: `apps/web/src/app/routing/AppRouter.test.tsx`
- Create: `apps/web/src/test/renderApp.tsx`
- Create: `apps/web/src/app/shell/AppShell.tsx`
- Create: `apps/web/src/app/shell/AppShell.css`
- Create: `apps/web/src/app/shell/AppShell.test.tsx`
- Modify: `apps/web/src/components/AppShell.tsx`
- Modify: `apps/web/src/pages/Dashboard.tsx`
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/routes.ts`
- Modify: `apps/web/src/i18n/ru.ts`
- Delete: `apps/web/src/nav.ts`
- Delete: `apps/web/src/nav-permissions.ts`
- Delete: `apps/web/src/nav-permissions.test.ts`
- Modify: `apps/web/scripts/check-nav.mjs`

**Interfaces:**

- Produces: `ROUTE_ELEMENTS: Record<AppRouteId, ReactElement>`, `RouteGate`, and the shell `<Outlet>` boundary.
- Consumes: RouteManifest/access policy, ThemeProvider theme/density state, Dialog, Button, Icon, ParkProvider, AuthProvider, report badge API.

- [ ] **Step 1: Write route and shell behavior tests**

Create `apps/web/src/test/renderApp.tsx` with a complete synthetic user factory and a location probe:

```tsx
import { render } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { vi } from 'vitest'
import { api, type User } from '../api'
import { AuthContext } from '../auth-context'
import { AppRouter } from '../app/routing/AppRouter'
import { ThemeProvider } from '../design-system/theme/ThemeProvider'

export function testUser(overrides: Partial<User> = {}): User {
  return {
    id: 1,
    username: 'test-user',
    role: 'operator',
    access_status: 'approved',
    permissions: [],
    parks: [],
    ...overrides,
  }
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}{location.search}</output>
}

export function renderApp(path: string, user: User | null) {
  vi.spyOn(api, 'reportsBadge').mockResolvedValue({ count: 0 })
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ThemeProvider>
        <AuthContext.Provider value={{
          user,
          loading: false,
          login: vi.fn(),
          refreshUser: vi.fn(),
          logout: vi.fn(),
        }}>
          <AppRouter />
          <LocationProbe />
        </AuthContext.Provider>
      </ThemeProvider>
    </MemoryRouter>,
  )
}
```

Use that helper to prove:

```tsx
const approvedOperator = testUser({
  permissions: ['nav.dashboard', 'nav.tasks', 'nav.emergency'],
  parks: [{ id: 7, name: 'Северный', tag: 'north' }],
})
renderApp('/dashboard', approvedOperator)
expect(screen.getByRole('heading', { name: /дашборд/i })).toBeVisible()
expect(screen.getByTestId('location')).toHaveTextContent('/overview')
expect(screen.getByRole('link', { name: 'Работа' })).toHaveAttribute('href', '/work')
expect(screen.getByRole('link', { name: 'Работа' }).querySelector('svg')).not.toBeNull()

renderApp('/operator?park=7', approvedOperator)
expect(screen.getByTestId('location')).toHaveTextContent('/overview?park=7')

renderApp('/overview', testUser({ access_status: 'pending' }))
expect(screen.getByTestId('location')).toHaveTextContent('/access/pending')

renderApp('/overview', testUser({ role: 'mechanic', parks: [], permissions: ['nav.dashboard'] }))
expect(screen.getByTestId('location')).toHaveTextContent('/mechanic/no-park')

renderApp('/emergency', testUser({ role: 'driver', permissions: ['nav.emergency'] }))
expect(screen.getByRole('navigation', { name: 'Основная навигация' }))
  .toHaveTextContent('Проверка робота')
expect(screen.queryByText('Аналитика')).not.toBeInTheDocument()
```

The shell test must open «Ещё», verify dialog focus, choose each of `Системная`, `Светлая`, and `Тёмная` through the `Тема оформления` control, and verify a permitted secondary route remains reachable. At a desktop width it must also choose `Комфортная` and `Компактная` through a `Плотность интерфейса` radio group and assert the resolved `data-density`. At `899px` and below the control explains `На телефоне используется комфортная плотность`, the DOM remains `comfortable`, and returning to desktop restores the saved compact choice.

Add keyboard-navigation regressions: Tab from the browser chrome reaches a first focusable link named `К содержанию`; activating it focuses `<main id="main-content" tabIndex={-1}>`. Clicking a manifest navigation link changes the route and focuses the destination `h1` (temporarily focusable with `tabIndex={-1}`), or the main region when the destination has no `h1`. The test must assert focus, not only URL/text, and must prove focus does not remain on a sidebar link that disappeared at the mobile breakpoint.

- [ ] **Step 2: Run focused tests and verify the old router fails expectations**

Run:

```bash
cd apps/web
npm test -- src/app/routing/AppRouter.test.tsx src/app/shell/AppShell.test.tsx
```

Expected: FAIL because `/overview`, the manifest router, and the new shell do not exist.

- [ ] **Step 3: Implement one route registry and one shell**

`AppRouter.tsx` is the only module that imports page components. Define:

```tsx
export const ROUTE_ELEMENTS: Record<AppRouteId, ReactElement> = {
  home: <Home />,
  login: <Login />,
  register: <Register />,
  'change-password': <ChangePassword />,
  'no-cabinet': <NoCabinet />,
  'access-pending': <OperatorPending />,
  'access-rejected': <OperatorRejected />,
  'mechanic-no-park': <MechanicNoPark />,
  overview: <Dashboard />,
  'operator-parks': <OperatorParks />,
  work: <Tasks />,
  robots: <RobotSearch />,
  'robot-check': <Emergency />,
  analytics: <Analytics />,
  reports: <Reports />,
  admin: <Admin />,
  'admin-tracker': <AdminTrackerWorkspace />,
  'admin-robot-check': <AdminEmergencyConfig />,
  'not-found': <RouteFallback />,
}
```

`RouteGate` receives `routeId` and applies loading, unauthenticated, password, status, park, and permission outcomes by calling `canAccessRoute` and `landingPathForUser`. `AppRouter` groups manifest entries by `surface`; shell entries render inside `<ParkProvider><AppShell /></ParkProvider>`. Every `legacyPaths` value renders `<Navigate to={canonicalPath} replace />`; preserve search parameters when they still have meaning.

The new AppShell:

- derives all links from `navigationForUser`;
- groups desktop links by `NavGroup`;
- uses the first four role-priority mobile items and places all remaining permitted items in `BottomSheet`;
- labels desktop and mobile nav `Основная навигация`;
- shows the selected park beside park-scoped data;
- uses SVG `Icon`, not Unicode glyphs;
- presents a radio group labelled `Тема оформления` with `Системная`, `Светлая`, `Тёмная`;
- presents a desktop-preference radio group labelled `Плотность интерфейса` with `Комфортная`, `Компактная`; on phone it remains understandable but the resolved state is always comfortable;
- preserves report badge behavior;
- renders a visually hidden-until-focused `К содержанию` link before navigation and exposes `<Outlet />` inside the stable `<main id="main-content" tabIndex={-1}>` landmark.

Track `location.pathname` in the shell. After a committed client-side route change, focus the new page's `h1` (give it temporary `tabIndex={-1}` when necessary), falling back to `#main-content`; use `preventScroll: true` only when the browser already restored a history position. Skip initial-load focus theft, and never focus on query-only changes such as park/filter/tab updates. Sequential mobile detail/back flows inherit this rule and are verified in Phase 2/Plan 05 E2E.

Use `min-height: var(--rp-control-min-size)` for controls and the exact approved media ranges in `AppShell.css`; both densities keep at least `44px` targets, while compact mode reduces only row padding, panel padding, and layout gaps. At 900–1199 px use a collapsible rail rather than the phone bottom bar. At 600–899 px and below use the role-prioritized bottom nav. Add safe-area padding with `env(safe-area-inset-bottom)`.

Replace `apps/web/src/components/AppShell.tsx` with:

```tsx
export { AppShell } from '../app/shell/AppShell'
```

Replace `App.tsx` with:

```tsx
import { AppRouter } from './app/routing/AppRouter'

export default function App() {
  return <AppRouter />
}
```

Rewrite `check-nav.mjs` to parse manifest `id` values and `ROUTE_ELEMENTS` keys, reject missing or duplicate IDs, and print `check-nav: ok (<count> route ids)`.

Before deleting `nav-permissions.ts`, migrate the still-mounted transitional `Dashboard.tsx` to the manifest policy. Remove its `navItemsForPermissions` import, import `navigationForUser` and `Icon`, and derive quick links exactly as follows:

```tsx
const quickLinks = user
  ? navigationForUser(user, 'desktop').filter((item) =>
      ['work', 'robot-check', 'reports', 'analytics', 'robots'].includes(item.id),
    )
  : []

{quickLinks.map((item) => (
  <Link className="chip chip-link" key={item.id} to={item.path}>
    <Icon name={item.icon} size={18} />
    {item.label}
  </Link>
))}
```

Keep its existing permission checks for Tracker actions until Plan 02 replaces the page. This migration is required before `nav-permissions.ts` disappears, so the Task 8 build is independently green.

Replace `routes.ts` with the complete temporary adapter needed by retained pre-Plan-04 pages:

```ts
import type { AccessUser } from './app/routing/accessPolicy'
import { landingPathForUser } from './app/routing/accessPolicy'

export const NO_CABINET_PATH = '/no-cabinet'
export const pathForUser = landingPathForUser

export function hasCabinet(user: AccessUser): boolean {
  return landingPathForUser(user) !== NO_CABINET_PATH
}
```

Do not delete `routes.ts` in this phase: `Home.tsx` and `NoCabinet.tsx` consume this adapter until Plan 04 replaces the onboarding screens; Plan 05 deletes it after both references are gone. After `Dashboard.tsx` compiles against `navigationForUser`, delete `nav.ts`, `nav-permissions.ts`, and `nav-permissions.test.ts`; their behavior is covered by manifest/access-policy, Dashboard quick-link, and shell tests.

- [ ] **Step 4: Verify routing, shell, nav consistency, and build**

Run:

```bash
cd apps/web
npm test -- src/app/routing src/app/shell
npm run check-nav
npm run lint
npm run build
npm test
```

Expected: focused and full tests pass, nav checker reports every manifest route registered once, and build succeeds.

- [ ] **Step 5: Commit the app frame**

```bash
git add apps/web/src/app apps/web/src/components/AppShell.tsx apps/web/src/pages/Dashboard.tsx apps/web/src/App.tsx apps/web/src/routes.ts apps/web/src/i18n/ru.ts apps/web/src/nav.ts apps/web/src/nav-permissions.ts apps/web/src/nav-permissions.test.ts apps/web/scripts/check-nav.mjs
git commit -m "feat(web): add role-aware responsive app shell"
```

---

### Task 9: Add deterministic Playwright, axe, and visual smoke coverage

**Files:**

- Create: `apps/web/playwright.config.ts`
- Create: `apps/web/e2e/support/mockApi.ts`
- Create: `apps/web/e2e/support/assertA11y.ts`
- Create: `apps/web/e2e/support/assertA11y.test.ts`
- Create: `apps/web/e2e/support/users.ts`
- Create: `apps/web/e2e/app-shell.spec.ts`
- Create: `apps/web/e2e/mock-api-contract.spec.ts`
- Create: `apps/web/e2e/app-shell.spec.ts-snapshots/*.png`
- Create: `apps/web/scripts/playwright-linux.sh`
- Modify: `apps/web/package.json`
- Modify: `apps/web/package-lock.json`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**

- Produces `MockResponse`, `MockRouteHandler`, `MockRoute`, `MockApiOptions`, `installMockApi`, and `assertNoSeriousA11yViolations` exactly as shared with Plans 02–04.
- Despite its retained compatibility name, `assertNoSeriousA11yViolations` rejects every axe rule tagged WCAG 2.0/2.1/2.2 A or AA regardless of axe impact; it is not limited to `serious`/`critical`.
- `MockRouteHandler` always receives a standards-based DOM `Request`, never Playwright's `APIRequest`/`Request`; query parsing, `json()`, and multipart `formData()` therefore have one stable contract.
- Browser tests intercept `/api/*`; they do not start or inspect the real API database.

- [ ] **Step 1: Install browser-test dependencies and write the smoke test first**

Run:

```bash
cd apps/web
npm install --save-dev @playwright/test @axe-core/playwright
```

Add scripts:

```json
{
  "test:e2e": "playwright test",
  "test:e2e:update": "playwright test --update-snapshots",
  "test:e2e:linux": "bash scripts/playwright-linux.sh test",
  "test:e2e:update:linux": "bash scripts/playwright-linux.sh update"
}
```

Create `apps/web/e2e/support/users.ts` with synthetic records only:

```ts
import type { Park, User } from '../../src/api'

export const northPark: Park = { id: 7, name: 'Северный парк', tag: 'north', is_active: true }

export const operatorUser: User = {
  id: 20,
  username: 'operator-e2e',
  role: 'operator',
  access_status: 'approved',
  permissions: ['nav.dashboard', 'nav.tasks', 'nav.robot_search', 'nav.emergency', 'nav.reports'],
  parks: [northPark],
}

export const mechanicUser: User = {
  id: 30,
  username: 'mechanic-e2e',
  role: 'mechanic',
  access_status: 'approved',
  permissions: ['nav.dashboard', 'nav.tasks', 'nav.robot_search', 'nav.emergency', 'nav.reports'],
  parks: [northPark],
}
```

Create `app-shell.spec.ts` with deterministic shell, theme, and density cases:

```ts
test('operator shell is accessible in the light theme', async ({ page }) => {
  const fontRequests: string[] = []
  page.on('request', (request) => {
    if (request.resourceType() === 'font') fontRequests.push(request.url())
  })
  await page.addInitScript(() => localStorage.setItem('robopark-theme', 'light'))
  await installMockApi(page, { user: operatorUser, parks: operatorUser.parks })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/overview')
  const appOrigin = new URL(page.url()).origin
  await expect(page.getByRole('navigation', { name: 'Основная навигация' })).toBeVisible()
  expect(await page.evaluate(() => document.fonts.load('450 16px "Manrope Variable"', 'Робопарк'))).not.toHaveLength(0)
  expect(fontRequests).not.toHaveLength(0)
  expect(fontRequests.every((url) => new URL(url).origin === appOrigin)).toBe(true)
  expect(fontRequests.join('\n')).not.toMatch(/fonts\.(?:googleapis|gstatic)\.com/i)
  await assertNoSeriousA11yViolations(page)
  await expect(page).toHaveScreenshot('operator-shell-light-1440.png', { animations: 'disabled' })
})

test('mechanic shell keeps primary actions at 390px in dark theme', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('robopark-theme', 'dark'))
  await installMockApi(page, { user: mechanicUser, parks: mechanicUser.parks })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/overview')
  await expect(page.getByRole('navigation', { name: 'Основная навигация' })).toBeVisible()
  await assertNoSeriousA11yViolations(page)
  await expect(page).toHaveScreenshot('mechanic-shell-dark-390.png', { animations: 'disabled' })
})

test('system theme is applied before paint and follows live OS changes without losing context', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.addInitScript(() => localStorage.setItem('robopark-theme', 'system'))
  await installMockApi(page, { user: operatorUser, parks: operatorUser.parks })
  await page.goto('/robots?park=7')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.getByLabel('Номер робота или ключ тикета').fill('ROBOPARK-42')
  const timing = await page.evaluate(() => ({
    bootstrap: window.__roboparkThemeBootstrappedAt,
    firstPaint: performance.getEntriesByType('paint')[0]?.startTime ?? Number.POSITIVE_INFINITY,
  }))
  expect(timing.bootstrap).toBeLessThanOrEqual(timing.firstPaint)

  await page.emulateMedia({ colorScheme: 'light' })
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await expect(page).toHaveURL(/\/robots\?park=7$/)
  await expect(page.getByLabel('Номер робота или ключ тикета')).toHaveValue('ROBOPARK-42')
})

test('desktop compact density becomes comfortable on phone and restores without losing context', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.addInitScript(() => {
    localStorage.setItem('robopark-theme', 'light')
    localStorage.setItem('robopark-density', 'compact')
  })
  await installMockApi(page, { user: operatorUser, parks: operatorUser.parks })
  await page.goto('/robots?park=7')
  await page.getByLabel('Номер робота или ключ тикета').fill('ROBOPARK-42')
  await expect(page.locator('html')).toHaveAttribute('data-density', 'compact')

  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.locator('html')).toHaveAttribute('data-density', 'comfortable')
  await expect(page.getByLabel('Номер робота или ключ тикета')).toHaveValue('ROBOPARK-42')
  await expect(page).toHaveURL(/\/robots\?park=7$/)

  await page.setViewportSize({ width: 1440, height: 900 })
  await expect(page.locator('html')).toHaveAttribute('data-density', 'compact')
  expect(await page.evaluate(() => localStorage.getItem('robopark-density'))).toBe('compact')
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
})
```

The tests must observe real `matchMedia` changes—do not reload between dark/light or wide/narrow transitions. Their required invariant is that appearance changes preserve route query and in-progress form state; phone density never becomes compact, while the desktop preference is retained.

The font assertion is intentional acceptance coverage: Cyrillic text must resolve through the bundled face, at least one font resource must be requested, and every such request must be same-origin. Do not reference browser `location` directly from the Playwright test process.

Create `mock-api-contract.spec.ts` before the helper exists. Register three custom handlers and call them with `page.evaluate`: GET `/api/contract/query?park=7`, POST JSON `{ issue: 'ROBOPARK-42' }`, and multipart `FormData` containing text field `kind=device_photo` plus `new File(['robot'], 'robot.txt', { type: 'text/plain' })`. Inside the handlers use only DOM APIs `new URL(request.url)`, `await request.json()`, and `await request.formData()`; return the observed method/query/value and multipart file `name`, `type`, `size`. Assert the browser receives exactly `GET/7`, `ROBOPARK-42`, and `{ name: 'robot.txt', type: 'text/plain', size: 5 }`. This is the shared contract test for every later Plan 03 report upload handler.

Create `assertA11y.test.ts` around an exported pure `wcagAaViolations` filter. A synthetic `moderate` violation tagged `wcag2aa` must be returned; `minor`/`moderate` items tagged only `best-practice` must not. A violation tagged `wcag22aa` must be returned even without an impact. No global rule exclusion is allowed; any future exception must name one exact rule, one exact locator, a reason, owner, expiry date, and a regression assertion that the exception does not match other nodes.

- [ ] **Step 2: Run Playwright and verify helper imports fail**

Run:

```bash
cd apps/web
npx playwright install chromium
npm test -- e2e/support/assertA11y.test.ts
npm run test:e2e
```

Expected: FAIL because `mockApi.ts`, `assertA11y.ts`, and their WCAG filter do not exist.

- [ ] **Step 3: Implement the deterministic browser-test contract**

Use these exact types in `mockApi.ts`:

```ts
export type MockResponse = {
  status?: number
  json?: unknown
  body?: string
  headers?: Record<string, string>
}
export type MockRouteHandler = (request: Request) => MockResponse | Promise<MockResponse>
export type MockRoute = {
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  path: string | RegExp
  handler: MockRouteHandler
}
export type MockApiOptions = {
  user?: User | null
  parks?: Park[]
  routes?: readonly MockRoute[]
}
```

`path` matches the browser pathname including `/api`; strings are exact and query is ignored, while RegExp is tested against the pathname. Custom routes take precedence. Defaults return:

```ts
const defaults: Record<string, MockResponse> = {
  '/api/auth/me': options.user
    ? { json: options.user }
    : { status: 401, json: { detail: 'Unauthorized' } },
  '/api/ops/maintenance': { json: { active: false, kind: null, operator: false } },
  '/api/reports/badge': { json: { count: 0 } },
  '/api/parks': { json: options.parks ?? [] },
}
```

Fulfill JSON responses with `contentType: 'application/json'`. Unmatched endpoints return `{ status: 404, json: { detail: 'mock_not_configured' } }` so missing fixtures cannot silently pass.

Adapt Playwright's request before invoking a custom handler; never cast it to the DOM type:

```ts
import type { Request as PlaywrightRequest } from '@playwright/test'

async function toHandlerRequest(request: PlaywrightRequest): Promise<Request> {
  const method = request.method()
  const bytes = method === 'GET' || method === 'HEAD'
    ? null
    : request.postDataBuffer()
  return new Request(request.url(), {
    method,
    headers: await request.allHeaders(),
    body: bytes ? new Uint8Array(bytes) : undefined,
  })
}
```

Within the `page.route('/api/**', ...)` callback, find the custom/default response from `new URL(playwrightRequest.url()).pathname`, then call `handler(await toHandlerRequest(playwrightRequest))`. Passing all headers preserves the multipart boundary; passing bytes rather than `postData()` preserves binary files. GET/HEAD never receive a body. The contract spec must fail if a Playwright request (whose URL is `url()`) leaks through.

Implement the axe helper:

```ts
import AxeBuilder from '@axe-core/playwright'
import { expect, type Page } from '@playwright/test'

const WCAG_AA_TAGS = new Set([
  'wcag2a', 'wcag2aa',
  'wcag21a', 'wcag21aa',
  'wcag22a', 'wcag22aa',
])

export function wcagAaViolations<T extends { tags: string[] }>(violations: T[]): T[] {
  return violations.filter((item) => item.tags.some((tag) => WCAG_AA_TAGS.has(tag)))
}

export async function assertNoSeriousA11yViolations(page: Page): Promise<void> {
  const result = await new AxeBuilder({ page }).analyze()
  const blocking = wcagAaViolations(result.violations)
  expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([])
}
```

The function name is frozen because every later plan imports it; the stricter tag-based implementation is the actual WCAG AA release contract. Do not suppress `moderate` color, name/role/value, reflow, target-size, or focus violations merely because axe assigned a lower impact.

Configure one Chromium project, `baseURL: 'http://127.0.0.1:4173'`, `webServer.command: 'npm run dev -- --host 127.0.0.1 --port 4173'`, retries only in CI, trace on first retry, screenshot comparison animations disabled, and this platform-neutral path (the Linux runner below is the only authority that writes or compares pixels):

```ts
snapshotPathTemplate: '{testDir}/{testFilePath}-snapshots/{arg}{ext}',
```

Create executable `scripts/playwright-linux.sh` so visual baselines always use the exact Playwright version locked by npm and the matching Microsoft Ubuntu image:

```bash
#!/usr/bin/env bash
set -euo pipefail

mode="${1:-test}"
shift || true
if [[ "$mode" != "test" && "$mode" != "update" ]]; then
  echo "usage: $0 <test|update> [playwright arguments...]" >&2
  exit 2
fi

playwright_version="$(node -p "require('./node_modules/@playwright/test/package.json').version")"
visual_workspace="$(mktemp -d "${TMPDIR:-/tmp}/robopark-playwright.XXXXXX")"
trap 'rm -rf "$visual_workspace"' EXIT

rsync -a \
  --exclude node_modules \
  --exclude dist \
  --exclude playwright-report \
  --exclude test-results \
  ./ "$visual_workspace/"

playwright_args=("$@")
if [[ "$mode" == "update" ]]; then
  playwright_args=(--update-snapshots "${playwright_args[@]}")
fi

docker run --rm --ipc=host \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp/robopark-playwright-home \
  -v "$visual_workspace:/work" \
  -w /work \
  "mcr.microsoft.com/playwright:v${playwright_version}-noble" \
  bash -lc 'npm ci && exec npx playwright test "$@"' robopark-playwright "${playwright_args[@]}"

if [[ "$mode" == "update" ]]; then
  rsync -a --prune-empty-dirs \
    --include '*/' --include '*-snapshots/***' --exclude '*' \
    "$visual_workspace/e2e/" ./e2e/
fi
```

Run `chmod +x apps/web/scripts/playwright-linux.sh`. The temporary copy prevents Linux `node_modules` from replacing the host installation. The official image supplies the matching Chromium and fonts. Never approve or commit a baseline generated by native macOS/Windows Playwright; native `test:e2e` remains useful only for semantic tests that contain no screenshots.

- [ ] **Step 4: Generate baselines, rerun without updates, and wire CI**

Run:

```bash
cd apps/web
npm run test:e2e:update:linux
npm run test:e2e:linux
```

Expected: all deterministic browser cases and axe checks pass on the second run with committed baselines.

Add CI steps after `./scripts/verify.sh`:

```yaml
      - name: Verify responsive browser journeys in the pinned Linux image
        working-directory: apps/web
        run: npm run test:e2e:linux
```

Then run the full foundation gate:

```bash
./scripts/verify.sh api
./scripts/verify.sh web
cd apps/web
npm run check:contrast
npm run test:e2e:linux
```

Expected: API and web verification, contrast gate, Playwright, screenshots, and axe all pass.

- [ ] **Step 5: Commit the browser verification foundation**

```bash
git add apps/web/package.json apps/web/package-lock.json apps/web/playwright.config.ts apps/web/scripts/playwright-linux.sh apps/web/e2e .github/workflows/ci.yml
git commit -m "test(web): add responsive accessibility smoke coverage"
```

---

## Foundation Completion Gate

Before starting Plan 02, verify from repository root:

```bash
git status --short
./scripts/verify.sh api
./scripts/verify.sh web
cd apps/web
npm run check:contrast
npm run test:e2e:linux
```

Required evidence:

- working tree contains only intentionally uncommitted plan-tracking edits, if any;
- API and web verification exit `0`;
- contrast checker reports every required pair at least `4.5`;
- Playwright reports all shell/theme/density smoke tests passing with no WCAG A/AA axe violation;
- system theme follows a live OS change without flash or lost URL/form state, and desktop compact density resolves to comfortable on phone then restores;
- `/dashboard`, `/operator`, `/tasks`, and `/robots/search` redirect to their new canonical paths;
- `/emergency` is visibly labelled «Проверка робота» until Plan 02 installs its canonical VIN route.
