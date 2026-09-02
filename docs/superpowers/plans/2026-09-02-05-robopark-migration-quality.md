# Robopark Migration and Quality Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the legacy UI after Plans 01–04, complete app-wide offline signalling and recovery verification, and prove the redesigned Robopark experience across roles, themes, devices, accessibility, builds, and deployment packaging.

**Architecture:** This plan runs only after every product domain has migrated to the new design-system and route contracts. It converts the remaining global overlays, enforces the absence of legacy UI through repository checks, adds cross-domain browser matrices, and leaves a single production architecture with compatibility redirects only at the routing boundary.

**Tech Stack:** React 19, TypeScript 6, React Router 7, Vite 8, Vitest, Testing Library, Playwright, axe-core, CSS custom properties, Python 3.12, FastAPI, pytest, Ruff, Docker Compose, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-02-robopark-product-redesign-design.md`

## Global Constraints

- Execute only after Plans 01, 02, 03, and 04 pass their completion gates.
- The main UX order is exactly: state → risk → next action.
- User-facing copy says «Проверка робота»; `emergency` remains only in documented API and legacy redirect names.
- Light, dark, and system themes share one semantic-token contract and preserve user preference.
- Desktop comfortable/compact density persists independently; widths through 899 px always resolve to comfortable and restore the saved desktop preference when widened.
- Test exactly 320, 390, 768, 1024, and 1440 px in both explicit themes.
- Native-host Playwright runs are semantic-only. Every visual assertion, snapshot update, and complete browser gate runs through the pinned Linux wrapper so committed baselines have one rendering authority.
- Interactive targets are at least 44×44 px; body/control text is at least 14 px; mobile form controls are at least 16 px.
- Text-bearing color combinations meet WCAG AA; status never relies on color alone.
- Do not invent robot photographs. Until owner-supplied licensed images arrive, the product uses the approved original schematic fallback.
- No E2E test reads developer runtime data, credentials, cookies, database rows, or secrets.
- Preserve legacy deep links through explicit redirects; do not preserve legacy component or CSS implementations.
- Every task has a mandatory index gate after its scoped `git add` and before `git commit`: run `git diff --cached --check`, inspect `git diff --cached --name-only`, and compare the result with that task's exact `Files`/intentional-snapshot allowlist. If any pre-existing, unrelated, runtime, credential, database, log, trace, or generated-download path is staged, stop without unstaging or overwriting another contributor's work.

---

## File Responsibility Map

### Cross-cutting application behavior

- Create `apps/web/src/app/connectivity/connectivity.ts` and `ConnectivityProvider.tsx`: distinguish browser connectivity from robot connectivity.
- Create `apps/web/src/app/connectivity/ConnectivityProvider.test.tsx` and `OfflineBanner.tsx`/`OfflineBanner.css`.
- Modify `apps/web/src/main.tsx`: mount ConnectivityProvider and the global browser-offline banner above every public, standalone, and shell route.
- Reuse without wrapping or duplicating Plan 02 `shared/api/classifyApiError.ts` and `shared/browser/useOnlineStatus.ts` in all cross-domain recovery tests.

### Global overlays

- Modify `apps/web/src/components/GlobalProgress.tsx`: use accessible design-system progress semantics.
- Create `apps/web/src/components/GlobalProgress.css`.
- Create `apps/web/src/components/GlobalProgress.test.tsx`.
- Modify `apps/web/src/components/ops/MaintenanceGate.tsx` and `MaintenanceOverlay.tsx`.
- Create `apps/web/src/components/ops/MaintenanceOverlay.css`.
- Modify `apps/web/src/components/ScreenshotGuard/ScreenshotGuard.tsx`, `ScreenshotGuardGate.tsx`, and `PersistentWatermark.tsx`.
- Create `apps/web/src/components/ScreenshotGuard/ScreenshotGuard.css`.

### Legacy removal and enforcement

- Create `apps/web/scripts/check-legacy-ui.mjs` and `check-legacy-ui.test.mjs`.
- Modify `apps/web/package.json`: add `check:legacy-ui`.
- Modify retained consumers before deletion: `pages/Home.tsx`, `auth.tsx`, `api.ts`, `components/tracker/IssueActionsPanel.tsx`, `IssueActionsPanel.test.tsx`, `RobotCheckPanel.tsx`, `RobotCheckPanel.test.tsx`, `domains/work/work.css`, and `domains/robots/robot-check.css`.
- Delete `apps/web/src/index.css`, the temporary Foundation adapters (`components/AppShell.tsx`, `components/PageShell.tsx`, `components/ui/Feedback.tsx`, `components/ui/Tabs.tsx`, `ParkProvider.tsx`, `park-context.ts`, and `routes.ts`), and the exact obsolete page/component/data cluster enumerated in Task 3.
- Modify `apps/web/src/app/routing/routeManifest.ts` and `AppRouter.tsx`: keep aliases, remove obsolete elements and placeholder routes.
- Modify `apps/web/src/app/routing/accessPolicy.test.ts`: keep the cumulative hand-authored policy table equal to the complete shell manifest.
- Keep the new `domains/**` route controllers, `shared/auth/protectedBrowserStorage.ts`, `domains/onboarding/components/ParkAccessPanel.tsx`, the retained capability-driven Tracker components, `components/emergency/InspectionMap.tsx`, `components/emergency/wheelHotspots.ts`, and the backend park-request API.

### Verification and documentation

- Create `apps/web/e2e/quality/role-matrix.spec.ts`.
- Create `apps/web/e2e/quality/responsive-themes.spec.ts` and committed snapshots.
- Create `apps/web/e2e/quality/connectivity-errors.spec.ts`.
- Create `apps/web/e2e/quality/deep-links.spec.ts`.
- Create `apps/web/e2e/quality/adaptive-accessibility.spec.ts`.
- Create `apps/web/scripts/check-build-budget.mjs`.
- Modify `apps/web/src/app/routing/AppRouter.tsx` and `AppRouter.test.tsx` only if measured chunk budgets require lazy domain boundaries.
- Modify `apps/web/package.json`: add `check:build-budget`.
- Modify `.github/workflows/ci.yml` and `scripts/verify.sh`.
- Modify `README.md` and `deploy/README.md`.

---

### Task 1: Promote browser connectivity to app-wide offline state

**Files:**

- Create: `apps/web/src/app/connectivity/connectivity.ts`
- Create: `apps/web/src/app/connectivity/ConnectivityProvider.tsx`
- Create: `apps/web/src/app/connectivity/ConnectivityProvider.test.tsx`
- Create: `apps/web/src/app/connectivity/OfflineBanner.tsx`
- Create: `apps/web/src/app/connectivity/OfflineBanner.css`
- Modify: `apps/web/src/main.tsx`

**Interfaces:**

- Produces `ConnectivityState`, `ConnectivityProvider`, `useConnectivity`, and `OfflineBanner`.
- Consumes the single Plan 02 `useOnlineStatus()` source, `Icon`, and the application entry point.
- Preserves Plan 02 `DomainError`/`classifyApiError` as the only HTTP recovery contract; this task must not create an app-level error mapper.

- [ ] **Step 1: Write connectivity context and banner tests**

```tsx
it('reacts to browser offline and online events', () => {
  const { result } = renderHook(() => useConnectivity(), { wrapper })
  act(() => window.dispatchEvent(new Event('offline')))
  expect(result.current.online).toBe(false)
  expect(result.current.changedAt).not.toBeNull()
  act(() => window.dispatchEvent(new Event('online')))
  expect(result.current.online).toBe(true)
})

it('announces only browser connectivity loss and disappears after recovery', () => {
  render(<OfflineBanner />, { wrapper })
  act(() => window.dispatchEvent(new Event('offline')))
  expect(screen.getByRole('status')).toHaveTextContent('Нет связи с сетью')
  expect(screen.getByRole('status')).toHaveTextContent('Сохранённые данные могут устареть')
  act(() => window.dispatchEvent(new Event('online')))
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})
```

The test setup must set `navigator.onLine` before each event, because JSDOM does not change it automatically. The banner text must never imply that a robot is offline.

- [ ] **Step 2: Run focused tests and verify missing modules fail**

Run:

```bash
cd apps/web
npx vitest run src/app/connectivity/ConnectivityProvider.test.tsx
```

Expected: FAIL because the connectivity context and banner modules do not exist.

- [ ] **Step 3: Implement browser-only connectivity and the global banner**

Use these exact types:

```ts
export type ConnectivityState = {
  online: boolean
  changedAt: number | null
}
```

Implement the provider by consuming, not reproducing, the Plan 02 hook:

```tsx
const ConnectivityContext = createContext<ConnectivityState | null>(null)

export function ConnectivityProvider({ children }: PropsWithChildren) {
  const online = useOnlineStatus()
  const previous = useRef(online)
  const [changedAt, setChangedAt] = useState<number | null>(null)

  useEffect(() => {
    if (previous.current !== online) {
      previous.current = online
      setChangedAt(Date.now())
    }
  }, [online])

  const value = useMemo(() => ({ online, changedAt }), [online, changedAt])
  return <ConnectivityContext.Provider value={value}>{children}</ConnectivityContext.Provider>
}

export function useConnectivity(): ConnectivityState {
  const value = useContext(ConnectivityContext)
  if (!value) throw new Error('useConnectivity must be used inside ConnectivityProvider')
  return value
}
```

`OfflineBanner` returns `null` while online. Offline it renders a semantic-token surface with `role="status"`, `Icon name="offline"`, title `Нет связи с сетью` and description `Сохранённые данные могут устареть. Проверяем подключение…`. It says only that the user's device has lost network access; it never says that a robot is offline.

Wrap the existing provider tree in `ConnectivityProvider` and render `OfflineBanner` in `main.tsx` immediately inside that provider and before the router content. This keeps it visible on `/login`, access-state pages, `/account`, and shell routes alike. Keep domain request failures on Plan 02 `classifyApiError(error, fallback)`; browser connectivity augments presentation but never changes authorization or robot status.

- [ ] **Step 4: Verify focused tests and the web build**

Run:

```bash
cd apps/web
npx vitest run src/app/connectivity/ConnectivityProvider.test.tsx
npm run lint
npm run build
```

Expected: tests, Oxlint, and build pass; OfflineBanner is distinguishable from every robot status.

- [ ] **Step 5: Commit cross-cutting recovery state**

```bash
git add apps/web/src/app/connectivity apps/web/src/main.tsx
git commit -m "feat(web): add explicit connectivity recovery state"
```

---

### Task 2: Migrate global overlays to the design system

**Files:**

- Modify: `apps/web/src/components/GlobalProgress.tsx`
- Create: `apps/web/src/components/GlobalProgress.test.tsx`
- Create: `apps/web/src/components/GlobalProgress.css`
- Modify: `apps/web/src/components/ops/MaintenanceGate.tsx`
- Modify: `apps/web/src/components/ops/MaintenanceOverlay.tsx`
- Create: `apps/web/src/components/ops/MaintenanceOverlay.css`
- Modify: `apps/web/src/components/ScreenshotGuard/ScreenshotGuard.tsx`
- Modify: `apps/web/src/components/ScreenshotGuard/ScreenshotGuardGate.tsx`
- Modify: `apps/web/src/components/ScreenshotGuard/PersistentWatermark.tsx`
- Create: `apps/web/src/components/ScreenshotGuard/ScreenshotGuard.css`
- Create: `apps/web/e2e/quality/global-overlays.spec.ts`
- Modify: existing tests under `apps/web/src/components/ops` and `ScreenshotGuard`.

**Interfaces:**

- Consumes `Dialog`, `LoadingState`, semantic tokens, and the Foundation `installMockApi` browser harness.
- Preserves existing API polling and screenshot-policy contracts until an API plan changes them.
- Produces no new domain API.

- [ ] **Step 1: Add global overlay accessibility tests**

```tsx
render(<MaintenanceOverlay status={{ active: true, kind: 'update', operator: false }} />)
expect(screen.getByRole('alertdialog', { name: /технические работы/i })).toBeVisible()
expect(document.querySelector('#root')).toHaveAttribute('inert')

render(<GlobalProgress active label="Обновляем данные" />)
expect(screen.getByRole('progressbar', { name: 'Обновляем данные' })).toHaveAttribute('aria-valuetext', 'Выполняется')
```

Extend screenshot-guard tests to verify its blocking dialog receives initial focus, Escape does not dismiss a mandatory policy warning, and watermark text is `aria-hidden` so it is not repeatedly announced.

Create `global-overlays.spec.ts` with light/dark parameterized cases. The maintenance case overrides `/api/ops/maintenance` with `{ active: true, kind: 'update', operator: false }`, opens `/login`, and asserts the `alertdialog`, focus containment, and absence of dismiss controls. The screenshot case installs an approved synthetic user with `screenshot_guard: true`, opens `/overview`, presses `PrintScreen`, asserts the policy `alertdialog`, verifies Escape/backdrop do not close it, then uses its explicit acknowledgement button. Both cases run `assertNoSeriousA11yViolations` after the overlay is visible; no test contacts a real API.

- [ ] **Step 2: Run focused tests and confirm current overlays fail the new semantics**

Run:

```bash
cd apps/web
npm test -- src/components/ops src/components/ScreenshotGuard src/components/GlobalProgress.test.tsx
```

Expected: FAIL because the current overlays lack the shared focus/inert and progress semantics.

- [ ] **Step 3: Rebuild overlay presentation without changing policy behavior**

Change `GlobalProgress` to accept:

```ts
export type GlobalProgressProps = {
  active?: boolean
  label?: string
}
```

When the existing resource store drives it, map active request count to `active`. Render a visually slim progress bar with `role="progressbar"`, accessible label, and indeterminate animation disabled under reduced motion.

Render maintenance through the shared Dialog focus machinery with `role` overridden to `alertdialog`, `dismissible={false}`, and no close action. Pause maintenance polling while `document.visibilityState === 'hidden'`; on visibility return, refresh immediately before restarting the 2-second active-page interval.

Keep screenshot blocking behavior but move layout to `ScreenshotGuard.css`, use shared Dialog semantics, keep mandatory policy dialogs non-dismissible, and keep watermark content out of the accessibility tree.

Every new stylesheet must use `--rp-*` semantic tokens and include dark-theme behavior without literal white panels.

- [ ] **Step 4: Verify overlay behavior in both themes**

Run:

```bash
cd apps/web
npm test -- src/components/ops src/components/ScreenshotGuard src/components/GlobalProgress.test.tsx
npm run lint
npm run build
npm run test:e2e -- --grep "maintenance|screenshot|progress"
```

Expected: unit/component tests and matching browser cases pass in light and dark themes.

- [ ] **Step 5: Commit global overlay migration**

```bash
git add apps/web/src/components/GlobalProgress.tsx apps/web/src/components/GlobalProgress.test.tsx apps/web/src/components/GlobalProgress.css apps/web/src/components/ops apps/web/src/components/ScreenshotGuard apps/web/e2e/quality/global-overlays.spec.ts
git commit -m "refactor(web): migrate global overlays to design system"
```

---

### Task 3: Delete the legacy UI and enforce a single architecture

**Files:**

- Create: `apps/web/scripts/check-legacy-ui.mjs`
- Create: `apps/web/scripts/check-legacy-ui.test.mjs`
- Modify: `apps/web/package.json`
- Modify: `apps/web/src/main.tsx`
- Modify: `apps/web/src/pages/Home.tsx`
- Create: `apps/web/src/pages/Home.test.tsx`
- Modify: `apps/web/src/auth.tsx`
- Modify: `apps/web/src/api.ts`
- Modify: `apps/web/src/app/routing/routeManifest.ts`
- Modify: `apps/web/src/app/routing/AppRouter.tsx`
- Modify: `apps/web/src/app/routing/AppRouter.test.tsx`
- Modify: `apps/web/src/app/routing/accessPolicy.test.ts`
- Modify: `apps/web/src/components/tracker/IssueActionsPanel.tsx`
- Modify: `apps/web/src/components/tracker/IssueActionsPanel.test.tsx`
- Modify: `apps/web/src/components/tracker/RobotCheckPanel.tsx`
- Modify: `apps/web/src/components/tracker/RobotCheckPanel.test.tsx`
- Modify: `apps/web/src/domains/work/work.css`
- Modify: `apps/web/src/domains/robots/robot-check.css`
- Delete: every path in the `bannedFiles` manifest below that still exists after Plans 01–04.

**Interfaces:**

- Consumes all replacement modules from Plans 01–04.
- Preserves Plan 02 `clearProtectedBrowserStorage()` and its canonical report-draft/recent-robots-v2 prefixes as the single auth session-boundary cleanup; legacy removal must not weaken it.
- Produces a recursive repository check that prevents reintroduction of every retired file, legacy import, Unicode navigation glyph, compatibility capability, and placeholder route.
- Keeps explicit routing-boundary redirects for supported old URLs, including `/operator/parks → /account?section=parks`.
- Keeps `Home`, capability-driven `IssueActionsPanel`/`IssueDetailPanel`/`IssueList`/`RobotCheckPanel`, `InspectionMap`, `wheelHotspots`, `ParkAccessPanel`, the v2 user-scoped recent-robot store, and all operator/admin park-request APIs.

- [ ] **Step 1: Add the failing legacy-enforcement script**

Create `check-legacy-ui.mjs` with exact banned files and source patterns:

```js
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const bannedFiles = [
  'src/index.css',
  'src/routes.ts',
  'src/components/AppShell.tsx',
  'src/components/PageShell.tsx',
  'src/components/ui/Feedback.tsx',
  'src/components/ui/Tabs.tsx',
  'src/components/ui/Tabs.test.tsx',
  'src/ParkProvider.tsx',
  'src/ParkProvider.test.tsx',
  'src/park-context.ts',
  'src/pages/ComingSoon.tsx',
  'src/pages/Dashboard.tsx',
  'src/pages/Tasks.tsx',
  'src/pages/MechanicTasks.tsx',
  'src/pages/OperatorBlockers.tsx',
  'src/pages/RobotSearch.tsx',
  'src/pages/Emergency.tsx',
  'src/pages/AdminTrackerWorkspace.tsx',
  'src/pages/OperatorParks.tsx',
  'src/components/tracker/IssueDrawer.tsx',
  'src/components/tracker/IssueFilters.tsx',
  'src/components/tracker/TaskBoard.tsx',
  'src/components/tracker/TaskBoard.test.tsx',
  'src/components/tracker/TrackerWorkspace.tsx',
  'src/components/emergency/CookieStaleStub.tsx',
  'src/components/emergency/EmergencyViewer.tsx',
  'src/components/emergency/RobotSchematic.tsx',
  'src/components/emergency/robotHud.ts',
  'src/components/emergency/robotHud.test.ts',
  'src/components/emergency/inspectionUrl.ts',
  'src/components/emergency/inspectionUrl.test.ts',
  'src/assets/robot-top.png',
  'src/components/parks/RequestParkModal.tsx',
  'src/pages/Reports.tsx',
  'src/pages/Analytics.tsx',
  'src/pages/Analytics.test.tsx',
  'src/pages/OperatorNowReport.tsx',
  'src/components/reports/ReportDetail.tsx',
  'src/components/reports/ReportForms.tsx',
  'src/components/reports/ReportList.tsx',
  'src/components/reports/report-utils.ts',
  'src/lib/recentRobots.ts',
  'src/lib/recentRobots.test.ts',
  'src/lib/robotSearch.ts',
  'src/lib/robotSearch.test.ts',

  // Plan 04 owns these deletions. Keeping them here prevents resurrection.
  'src/pages/Login.tsx',
  'src/pages/Register.tsx',
  'src/pages/ChangePassword.tsx',
  'src/pages/OperatorPending.tsx',
  'src/pages/OperatorRejected.tsx',
  'src/pages/MechanicNoPark.tsx',
  'src/pages/NoCabinet.tsx',
  'src/pages/Admin.tsx',
  'src/pages/AdminEmergencyConfig.tsx',
  'src/components/admin/AdminUsersPanel.tsx',
  'src/components/admin/AdminRolesPanel.tsx',
  'src/components/admin/AdminOpsPanel.tsx',
  'src/components/ui/AuthBrand.tsx',
  'src/components/ui/PasswordField.tsx',
  'src/components/ui/RolePicker.tsx',
]
const existing = bannedFiles.filter((file) => existsSync(join(root, file)))

function walk(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const absolute = join(directory, entry.name)
    return entry.isDirectory() ? walk(absolute) : [absolute]
  })
}

const sourceEntries = [
  { file: 'index.html', text: readFileSync(join(root, 'index.html'), 'utf8') },
  ...walk(join(root, 'src'))
    .filter((file) => /\.(?:css|ts|tsx)$/.test(file))
    .map((file) => ({ file: relative(root, file), text: readFileSync(file, 'utf8') })),
]

export const bannedSourcePatterns = [
  { name: 'legacy routes helper', pattern: /from\s+['"](?:(?:\.\.?\/)+)routes['"]/ },
  { name: 'legacy AppShell adapter', pattern: /from\s+['"][^'"]*\/components\/AppShell['"]/ },
  { name: 'legacy PageShell', pattern: /from\s+['"][^'"]*\/PageShell['"]/ },
  { name: 'legacy Feedback', pattern: /from\s+['"][^'"]*\/ui\/Feedback['"]/ },
  { name: 'legacy Tabs', pattern: /from\s+['"][^'"]*\/ui\/Tabs['"]/ },
  { name: 'legacy root ParkProvider', pattern: /from\s+['"](?:(?:\.\.?\/)+)ParkProvider['"]/ },
  { name: 'legacy park context', pattern: /from\s+['"](?:(?:\.\.?\/)+)park-context['"]/ },
  { name: 'legacy ComingSoon', pattern: /from\s+['"][^'"]*\/pages\/ComingSoon['"]/ },
  { name: 'legacy recent robots', pattern: /from\s+['"][^'"]*\/lib\/recentRobots['"]/ },
  { name: 'legacy robot search', pattern: /from\s+['"][^'"]*\/lib\/robotSearch['"]/ },
  { name: 'legacy global stylesheet', pattern: /['"][^'"]*\/index\.css['"]/ },
  { name: 'native confirmation dialog', pattern: /\b(?:(?:window|globalThis)\s*\.\s*)?confirm\s*\(/ },
  { name: 'external Google Fonts host', pattern: /\bfonts\.(?:googleapis|gstatic)\.com\b/ },
]

export function scanBannedSourcePatterns(entries) {
  return bannedSourcePatterns.flatMap(({ name, pattern }) =>
    entries
      .filter(({ text }) => pattern.test(text))
      .map(({ file }) => ({ file, pattern: name })),
  )
}

const foundPatterns = scanBannedSourcePatterns(sourceEntries)

const issueActions = join(root, 'src/components/tracker/IssueActionsPanel.tsx')
if (existsSync(issueActions) && /\bcanWrite\b/.test(readFileSync(issueActions, 'utf8'))) {
  foundPatterns.push({
    file: 'src/components/tracker/IssueActionsPanel.tsx',
    pattern: 'Phase 2 canWrite compatibility capability',
  })
}

const bannedGlyphs = ['◧', '☰', '⌕', '⚑', '⊕', '◔', '✉', '✦']
const foundGlyphs = sourceEntries.flatMap(({ file, text }) =>
  bannedGlyphs.filter((glyph) => text.includes(glyph)).map((glyph) => ({ file, glyph })),
)

const manifest = readFileSync(join(root, 'src/app/routing/routeManifest.ts'), 'utf8')
const placeholderPaths = ['/map', '/learning', '/help'].filter((path) =>
  manifest.includes(`path: '${path}'`) || manifest.includes(`path: "${path}"`),
)

if (existing.length || foundPatterns.length || foundGlyphs.length || placeholderPaths.length) {
  console.error(JSON.stringify({ existing, foundPatterns, foundGlyphs, placeholderPaths }, null, 2))
  process.exit(1)
}

console.log('check-legacy-ui: ok')
```

Export the pure pattern scanner from `check-legacy-ui.mjs` and put filesystem scanning behind a direct-execution guard so `check-legacy-ui.test.mjs` can import it without scanning or exiting. The regression test passes synthetic `.tsx` and `.css` entries containing `window.confirm('delete')`, `globalThis.confirm('delete')`, bare `confirm('delete')`, `https://fonts.googleapis.com/css2`, and `https://fonts.gstatic.com/s/manrope.woff2`; assert the scanner reports `native confirmation dialog` and `external Google Fonts host` for every matching sample, while a local `@fontsource-variable/manrope` import is clean. This makes both the browser-native confirmation ban and the no-external-font rule executable rather than prose-only.

Add `"check:legacy-ui": "node --test scripts/check-legacy-ui.test.mjs && node scripts/check-legacy-ui.mjs"` to package scripts.

- [ ] **Step 2: Run the check and verify it finds the current legacy files**

Run `npm run check:legacy-ui` from `apps/web`.

Expected: FAIL and list every compatibility file still present after Plans 01–04, every retained consumer that still imports one, and `canWrite` until the Phase 2 adapter is removed.

- [ ] **Step 3: Migrate retained consumers before deleting their dependencies**

First inventory every live reference:

```bash
cd apps/web
rg -n "components/AppShell|components/PageShell|ui/Feedback|ui/Tabs|ParkProvider|park-context|ComingSoon|lib/recentRobots|lib/robotSearch|canWrite" src
```

Keep `pages/Home.tsx`, but remove its temporary `routes.ts` and `Feedback` dependencies:

First create `Home.test.tsx` with a `MemoryRouter`, `AuthContext.Provider`, and location probe. Assert that a loading session renders `getByRole('status', { name: 'Загружаем профиль' })`, an anonymous session redirects to `/login`, and an approved royal user with `nav.dashboard` redirects to `/overview`. Run it before editing `Home.tsx`; it must fail because the old component exposes `ru.loading` through `Spinner` and still delegates to `routes.ts`.

```tsx
import { Navigate } from 'react-router-dom'
import { landingPathForUser } from '../app/routing/accessPolicy'
import { useAuth } from '../auth-context'
import { LoadingState } from '../design-system/feedback/AsyncState'

export function Home() {
  const { user, loading } = useAuth()
  if (loading) return <LoadingState label="Загружаем профиль" variant="page" />
  return <Navigate replace to={user ? landingPathForUser(user) : '/login'} />
}
```

In `auth.tsx`, delete only the import and three calls for the **legacy unscoped** `clearRecentRobots`; keep every `resourceStore.clearAll()` and `clearProtectedBrowserStorage()` call. The Plan 02 canonical helper must still run before every login attempt, on refresh `401`, and in logout `finally`, deleting **all** `robopark.recentRobots.v2.` and `robopark:report-draft:` namespaces. Do not replace it with current-user cleanup: a deleted account may be recreated with the same numeric ID. Successful boot refresh/reload and transient offline/timeout/5xx continue to preserve same-session draft/recents.

Make the Phase 2 capability contract final in `IssueActionsPanel.tsx`:

```tsx
type IssueActionsPanelProps = {
  issueKey?: string
  capabilities: TrackerIssueCapabilities
  transitions: TrackerTransition[]
  currentUser?: string
  issueUrl?: string
  onComment: (text: string) => Promise<void>
  onAttach?: (file: File) => Promise<void>
  onAssign: (assignee: string) => Promise<void>
  onUnassign: () => Promise<void>
  onTransition: (transition: string) => Promise<void>
  onClose: () => Promise<void>
}
```

Use `IssueActionsPanelProps` on the existing named destructuring signature and destructure exactly the twelve fields above; do not retain a rest-prop compatibility path. Delete the `effectiveCapabilities = capabilities ?? { ... }` block from Plan 02. Compute the read-only case exactly:

```tsx
const hasInternalAction =
  capabilities.comment ||
  capabilities.assign ||
  capabilities.unassign ||
  capabilities.transition ||
  capabilities.close ||
  (capabilities.attach && Boolean(onAttach))
```

When `hasInternalAction` is false, render the existing read-only message plus the sanitized Tracker external link. Change the six existing JSX conditions exactly: attachment to `capabilities.attach && onAttach`, comment to `capabilities.comment`, transitions to `capabilities.transition && transitions.length > 0`, assignment form/self-assignment to `capabilities.assign`, unassign button to `capabilities.unassign`, and close button plus its `ConfirmDialog` to `capabilities.close`. Render the shared assignment action group only when `capabilities.assign || capabilities.unassign`. In the assignee suggestion effect, return after `setSuggestions([])` when `capabilities.assign` is false, and add `capabilities.assign` to its dependency list. Update its tests and every retained caller so `capabilities` is required. Preserve the per-action behavior, controlled close confirmation, attachment validation, object-URL cleanup, and safe Tracker URL. The recursive check must report `canWrite` if the adapter is accidentally restored.

Keep `RobotCheckPanel.tsx`, but replace `PageShell.Alert` and `ui/Feedback.Spinner` with Foundation `LoadingState` and `StatusBadge`: checking is an inline `role="status"`, the zero-findings state is `tone="success"`, and findings are inside one `role="alert"` with `tone="critical"`. Keep the canonical `/robots/:vin/check` link and its accessible name. Update `RobotCheckPanel.test.tsx` to assert those roles and copy.

Before deleting `index.css`, move all still-used `.issue-*` and `.robot-check*` selector families, including their 320/390 responsive rules, into `domains/work/work.css`. Move all `.inspection-map*` selectors and their responsive/reduced-motion rules into `domains/robots/robot-check.css`. Translate every declaration to the owning Plan 01 `--rp-*` semantic token; do not copy old theme selectors or raw colors. Remove the `index.css` import from `main.tsx` only after both domain test suites render with their colocated CSS.

After `pages/OperatorParks.tsx` is detached, run `rg -n "operatorParks" src`. If the only match is `api.operatorParks`, remove that client method from `api.ts`; keep `availableParks`, `operatorParkRequests`, `requestPark`, `adminParkRequests`, `resolveParkRequest`, `ParkAccessPanel`, `ParkRequestQueue`, and all corresponding backend routes.

Remove `/map`, `/learning`, and `/help` placeholder records/elements. Replace references to retired page elements in `ROUTE_ELEMENTS` with the completed Plans 02–04 domain controllers. Compatibility remains routing metadata or a focused redirect component only; no migrated URL may mount a retired page.

- [ ] **Step 4: Delete the exact legacy cluster and preserve redirects**

Delete these paths atomically after the reference migrations compile:

```bash
cd apps/web
git rm \
  src/index.css src/routes.ts src/components/AppShell.tsx src/components/PageShell.tsx \
  src/components/ui/Feedback.tsx src/components/ui/Tabs.tsx src/components/ui/Tabs.test.tsx \
  src/ParkProvider.tsx src/ParkProvider.test.tsx src/park-context.ts src/pages/ComingSoon.tsx \
  src/pages/Dashboard.tsx src/pages/Tasks.tsx src/pages/MechanicTasks.tsx \
  src/pages/OperatorBlockers.tsx src/pages/RobotSearch.tsx src/pages/Emergency.tsx \
  src/pages/AdminTrackerWorkspace.tsx src/pages/OperatorParks.tsx \
  src/components/tracker/IssueDrawer.tsx src/components/tracker/IssueFilters.tsx \
  src/components/tracker/TaskBoard.tsx src/components/tracker/TaskBoard.test.tsx \
  src/components/tracker/TrackerWorkspace.tsx \
  src/components/emergency/CookieStaleStub.tsx src/components/emergency/EmergencyViewer.tsx \
  src/components/emergency/RobotSchematic.tsx src/components/emergency/robotHud.ts \
  src/components/emergency/robotHud.test.ts src/components/emergency/inspectionUrl.ts \
  src/components/emergency/inspectionUrl.test.ts src/assets/robot-top.png \
  src/components/parks/RequestParkModal.tsx \
  src/pages/Reports.tsx src/pages/Analytics.tsx src/pages/Analytics.test.tsx \
  src/pages/OperatorNowReport.tsx src/components/reports/ReportDetail.tsx \
  src/components/reports/ReportForms.tsx src/components/reports/ReportList.tsx \
  src/components/reports/report-utils.ts src/lib/recentRobots.ts \
  src/lib/recentRobots.test.ts src/lib/robotSearch.ts src/lib/robotSearch.test.ts
```

Do **not** delete `components/tracker/IssueActionsPanel.tsx`, `IssueDetailPanel.tsx`, `IssueList.tsx`, `RobotCheckPanel.tsx`, `commentChat.ts`, `issue-utils.ts`, `robotHealth.ts`, their retained tests, `components/emergency/InspectionMap.tsx`, `components/emergency/wheelHotspots.ts`, `lib/resource.ts`, `lib/safeUrl.ts`, `lib/passwordChecks.ts`, `reports-badge.ts`, maintenance/screenshot logic, or `domains/onboarding/components/ParkAccessPanel.tsx`.

Keep these compatibility redirects in `AppRouter.test.tsx`, including query scrubbing for the retired operator page:

```tsx
const fullyPermittedUser = testUser({
  role: 'royal',
  access_status: 'approved',
  permissions: [
    'nav.dashboard', 'nav.tasks', 'nav.robot_search', 'nav.emergency',
    'nav.reports', 'nav.analytics', 'nav.admin', 'nav.admin.tracker',
    'nav.admin.emergency',
  ],
})
const pendingUser = testUser({ role: 'operator', access_status: 'pending' })
const rejectedUser = testUser({ role: 'operator', access_status: 'rejected' })
const operatorUser = testUser({
  role: 'operator',
  access_status: 'approved',
  permissions: ['nav.dashboard'],
  parks: [{ id: 7, name: 'Северный', tag: 'north' }],
})

it.each([
  { from: '/dashboard', expected: '/overview', user: fullyPermittedUser },
  { from: '/operator?park=7', expected: '/overview?park=7', user: operatorUser },
  { from: '/tasks', expected: '/work', user: fullyPermittedUser },
  { from: '/robots/search', expected: '/robots', user: fullyPermittedUser },
  { from: '/emergency?q=447', expected: '/robots/YASADR00000000447/check', user: fullyPermittedUser },
  { from: '/operator/pending', expected: '/access/pending', user: pendingUser },
  { from: '/operator/rejected', expected: '/access/rejected', user: rejectedUser },
  { from: '/operator/parks?token=synthetic', expected: '/account?section=parks', user: operatorUser },
  { from: '/admin/emergency/config', expected: '/admin/robot-check', user: fullyPermittedUser },
])('redirects $from to $expected', async ({ from, expected, user }) => {
  renderApp(from, user)
  await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(expected))
})
```

After the final redirects and deletions are represented, add this release invariant beside the hand-authored cumulative `protectedRoutes` table in `accessPolicy.test.ts`:

```ts
it('keeps the protected policy table equal to the complete shell manifest', () => {
  const policyIds = protectedRoutes.map(({ id }) => id)
  const shellIds = ROUTE_MANIFEST
    .filter(({ surface }) => surface === 'shell')
    .map(({ id }) => id)

  expect(new Set(policyIds)).toEqual(new Set(shellIds))
  expect(policyIds).toHaveLength(new Set(policyIds).size)
  expect(shellIds).toHaveLength(new Set(shellIds).size)
})
```

Keep `protectedRoutes` independent and hand-authored; only this equality assertion reads `ROUTE_MANIFEST`. It fails if any protected shell route is untested, if a deleted shell route remains in the manifest, or if either side contains duplicate IDs. Run the existing Cartesian permission/status/password/park cases unchanged over the completed table.

Do not keep a compatibility React component for any migrated page; compatibility lives only in redirect metadata or a small redirect element.

- [ ] **Step 5: Verify retained modules and prove no legacy source remains**

Run:

```bash
cd apps/web
npx vitest run \
  src/pages/Home.test.tsx \
  src/components/tracker/IssueActionsPanel.test.tsx \
  src/components/tracker/RobotCheckPanel.test.tsx \
  src/app/routing/AppRouter.test.tsx \
  src/app/routing/accessPolicy.test.ts
npm run check:legacy-ui
npm run check-nav
npm run check:contrast
npm run lint
npm run build
npm test
```

Expected: every command exits `0`; `check-legacy-ui` prints only `check-legacy-ui: ok`.

- [ ] **Step 6: Commit removal**

```bash
git add -A apps/web/src apps/web/scripts/check-legacy-ui.mjs apps/web/scripts/check-legacy-ui.test.mjs apps/web/package.json apps/web/package-lock.json
git commit -m "refactor(web): remove legacy interface architecture"
```

---

### Task 4: Add the complete role, device, theme, deep-link, and recovery matrix

**Files:**

- Create: `apps/web/e2e/quality/role-matrix.spec.ts`
- Create: `apps/web/e2e/quality/responsive-themes.spec.ts`
- Create: `apps/web/e2e/quality/connectivity-errors.spec.ts`
- Create: `apps/web/e2e/quality/deep-links.spec.ts`
- Create: `apps/web/e2e/quality/adaptive-accessibility.spec.ts`
- Create: screenshot baselines under the configured Playwright snapshot directory.
- Modify: `apps/web/e2e/support/users.ts`
- Modify: `apps/web/e2e/support/mockApi.ts`

**Interfaces:**

- Consumes the Plan 01 `installMockApi` and `assertNoSeriousA11yViolations` contracts.
- Consumes canonical routes and completed domain mock fixtures from Plans 02–04.
- Produces deterministic acceptance coverage for all approved form factors and roles.

- [ ] **Step 1: Write a matrix that initially exposes missing cases**

Define exact constants:

```ts
const viewports = [
  { name: 'phone-320', width: 320, height: 720 },
  { name: 'phone-390', width: 390, height: 844 },
  { name: 'tablet-768', width: 768, height: 1024 },
  { name: 'split-1024', width: 1024, height: 768 },
  { name: 'desktop-1440', width: 1440, height: 900 },
] as const
const themes = ['light', 'dark'] as const
const roles = [
  { name: 'mechanic', start: '/overview', heading: 'Что требует внимания в смене' },
  { name: 'operator', start: '/overview', heading: 'Что мешает работе парка' },
  { name: 'driver', start: '/overview', heading: 'Можно ли безопасно продолжать работу' },
  { name: 'admin', start: '/overview', heading: 'Готовность людей и системы' },
  { name: 'royal', start: '/overview', heading: 'Главный риск доступных парков' },
] as const
```

`role-matrix.spec.ts` runs semantic navigation assertions for every role × viewport × theme combination (50 cases) without screenshots. For each role, first assert `getByRole('heading', { name: role.heading })`, then prove its main operation is reachable in at most two link/button clicks from `start`.

Add a separate capability-driven custom-role E2E matrix; it is intentionally not folded into the 50 fixed system-role cases. Define an approved raw role slug `field_lead`, assign park `7`, and grant exactly `nav.dashboard`, `nav.tasks`, `nav.robot_search`, `nav.emergency`, `nav.reports`, `reports.create`, `nav.analytics`, and the backend data capability `tracker.read`. At 390 and 1440 px in both themes, prove its landing page renders, its permitted Work/Robots/Reports/Analytics destinations are reachable with scoped data, the park scope stays `7`, and every administration destination remains absent. Add a second direct-load case with the same slug but no assigned park and assert the scope-protected route is rejected without treating the slug as a system role.

`responsive-themes.spec.ts` uses the operator fixture as the stable visual representative and creates ten baselines. For each viewport/theme pair, assert:

```ts
await page.setViewportSize(viewport)
await page.addInitScript((theme) => localStorage.setItem('robopark-theme', theme), theme)
await page.goto('/overview')
expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
await assertNoSeriousA11yViolations(page)
await expect(page).toHaveScreenshot(`${viewport.name}-${theme}.png`, {
  animations: 'disabled',
  maxDiffPixelRatio: 0.005,
})
```

The 320/390 tests additionally inspect every visible button/link/input bounding box and fail when width or height is below 44 px, excluding inline prose links marked `data-inline-link="true"`.

- [ ] **Step 2: Run the new matrix without updating snapshots**

Run:

```bash
cd apps/web
npm run test:e2e -- e2e/quality/role-matrix.spec.ts
npm run test:e2e:linux -- e2e/quality/responsive-themes.spec.ts --project=chromium
```

Expected: FAIL for missing screenshot baselines and expose any remaining route, overflow, touch-target, or accessibility failure.

- [ ] **Step 3: Add deep-link and recovery assertions**

The deep-link suite must load these URLs directly, reload them, and verify the same selected context remains:

```ts
const deepLinks = [
  '/work/ROBOPARK-42?park=7&status=open',
  '/robots/YASADR00000000447',
  '/robots/YASADR00000000447/check?park=7&tab=wheels',
  '/reports/41?park=7',
  '/admin/access?status=pending',
] as const
```

In the same browser suite, load the legacy landing URL `/operator?park=7` directly and assert a replace redirect to exactly `/overview?park=7`; reload the result and prove park `7` remains selected. A legacy compatibility redirect may scrub known secret-like keys such as `token`, but must retain this meaningful park query.

The recovery suite overrides mock routes to return `401`, `403`, `404`, `409`, `503 tracker_token_not_configured`, plain `500`, a request that exceeds the UI timeout before succeeding on retry, browser offline, stale robot telemetry, and robot offline. Assert the exact recovery title and action for each, assert the timeout request is cancelled before retry, and verify browser offline never changes the robot's own last-known status label.

For the timeout case, install Playwright's page clock before navigation and add an init script that wraps `window.fetch`: only the first `/api/dashboard/summary` request returns a promise whose `abort` listener rejects with `DOMException('Aborted', 'AbortError')`; subsequent calls delegate to the original fetch and therefore reach `installMockApi`. Advance the page clock by `30_000ms`, assert `Сервис не ответил вовремя`, click the retry action, and assert the mocked summary renders. This proves both deadline cancellation and recovery without a 30-second wall-clock wait.

Add two stale-while-revalidate journeys to `connectivity-errors.spec.ts`; a generic offline banner is not enough:

- Open `/work/ROBOPARK-42?park=7`, let the list, selected issue, and comments render from a successful response, then make the next list/detail/comment revalidation fail with an explicitly transient offline, timeout, or `500` response. Trigger refresh and assert issue `ROBOPARK-42`, its selected detail, and existing comments stay mounted beside the localized non-blocking alert, `StaleBadge`, and retry action. Restore the mock, retry, and assert the freshness marker returns without losing the route query.
- Open `/reports/41?park=7`, render the queue and selected report, then fail both list and detail revalidation with an explicitly transient offline, timeout, or `500` response. Assert report `41` and its detail stay mounted beside their stale/offline indicators and retry actions. Restore the mock and prove retry updates the same view. Add cold-load controls for Work and Reports which still render the full `ErrorState` when no cached data exists.
- Seed user-scoped Work and Reports cache for the current user and a second user, then return `403` after revoking the current user's permission or park scope. Assert protected cached content and actions disappear immediately, every affected current-user resource-cache key is purged, the second user's resource keys remain untouched, and the UI starts scope recovery instead of displaying a stale payload. In a separate `401` case, seed report-draft and recent-robots-v2 local-storage keys for multiple user IDs; assert the global protected-resource cache, **all** protected browser-storage prefixes, and session are cleared before the user leaves the protected route. A `403` alone does not end the session and therefore does not call global browser-storage cleanup.
- Add a delete/recreate identity regression in `connectivity-errors.spec.ts`: seed a draft and recent robot for old account `id=3`, run logout (including a mocked network failure), then start login for a newly created account that also receives numeric `id=3`. Inspect storage inside the login route handler and assert both prefix families are already empty before authentication; after landing, `/reports/new` has no old title/body/idempotency checkpoint and `/robots` has no old recent card. Follow with a successful same-session draft edit + reload and an offline/reconnect refresh to prove neither path invokes cleanup or loses the current draft.

Add an ambiguous report-create recovery journey using the Phase 3 fixture: let the server commit a report and drop the first response, reload the composer, then retry with the exact persisted idempotency key. Assert two HTTP attempts resolve to one database/report row and the UI opens that canonical report rather than creating a duplicate.

Create `adaptive-accessibility.spec.ts` as a semantic-only suite over these exact product surfaces, each with the appropriate deterministic user and mock API capabilities:

```ts
const adaptiveSurfaces = [
  { name: 'onboarding', path: '/account?section=parks' },
  { name: 'administration', path: '/admin/access?status=pending' },
  { name: 'reports', path: '/reports?park=7' },
  { name: 'analytics', path: '/analytics?park=7&days=7' },
  { name: 'robot detail', path: '/robots/YASADR00000000447?park=7' },
  { name: 'report composer', path: '/reports/new?park=7' },
] as const
```

For every surface, first emulate `reducedMotion: 'reduce'` before navigation at 320 px, wait for the stable heading and principal action, assert `matchMedia('(prefers-reduced-motion: reduce)').matches`, assert no nonessential document animation remains running after interaction, verify every visible target is at least 44×44 px, verify no document-level horizontal overflow, and run `assertNoSeriousA11yViolations`. Exercise one representative state change per surface (open/close a panel or dialog, switch a tab/filter, or select a result) so the assertion covers transitions, not only the idle page.

Then run every same surface at 1440 px with the root font size set to exactly `200%`, using the Foundation reflow method rather than CSS `zoom`. Assert the heading, scope, current state/risk, and principal action remain visible or keyboard-reachable; assert `document.documentElement.scrollWidth <= window.innerWidth`; and run axe again. This extends the Phase 2 Overview/Work checks to onboarding, administration, reports, analytics, robot detail, and the report composer.

Add a live system-theme/no-flash case in the same suite. Before navigation set `robopark-theme=system` and emulate a dark OS scheme, load `/robots?park=7`, fill `Номер робота или ключ тикета` with `ROBOPARK-42`, wait for the `first-contentful-paint` entry, and assert `data-theme="dark"`, `window.__roboparkThemeBootstrappedAt <= firstContentfulPaint.startTime`, the unchanged `/robots?park=7` URL, and the in-progress input value. Change the emulated OS scheme to light without reload; assert `data-theme="light"`, the stored preference is still `system`, and the URL/input remain unchanged. Explicit light/dark persistence remains covered by the ten visual cases.

Add the final responsive-density persistence case alongside it. Start at 1440 px with `robopark-density=compact`, load `/robots?park=7`, and fill `Номер робота или ключ тикета` with the unsubmitted value `ROBOPARK-42`. Assert `data-density="compact"`; resize the same page to 390 px and assert resolved `data-density="comfortable"`; resize back to 1440 px and assert compact is restored. At every stage assert local storage still contains `compact`, the URL is still `/robots?park=7`, and the input value is still `ROBOPARK-42`. Do not reload: this must exercise the real Foundation `matchMedia` subscription.

Add one keyboard/history journey: on `/robots?park=7&q=447`, the first Tab focuses `К содержанию`; Enter focuses `<main>`. Follow the accessible result link to `YASADR00000000447`, assert the destination `h1` receives focus, invoke browser Back, and assert the list URL/query/search value are restored and the list heading receives route focus. Repeat the forward/back transition once at 390 px after the selected desktop navigation item has moved into the mobile shell, proving focus never stays on an element removed by the breakpoint.

Generate snapshots only after semantic assertions pass:

```bash
cd apps/web
npm run test:e2e -- e2e/quality/role-matrix.spec.ts e2e/quality/connectivity-errors.spec.ts e2e/quality/deep-links.spec.ts e2e/quality/adaptive-accessibility.spec.ts --project=chromium
npm run test:e2e:update:linux -- e2e/quality/responsive-themes.spec.ts --project=chromium
npm run test:e2e:linux -- e2e/quality/responsive-themes.spec.ts --project=chromium
```

- [ ] **Step 4: Run the matrix twice and inspect zero flaky retries**

Run:

```bash
cd apps/web
npm run test:e2e -- e2e/quality/role-matrix.spec.ts e2e/quality/connectivity-errors.spec.ts e2e/quality/deep-links.spec.ts e2e/quality/adaptive-accessibility.spec.ts --project=chromium
npm run test:e2e:linux -- e2e/quality --project=chromium
npm run test:e2e:linux -- e2e/quality --project=chromium
```

Expected: the native semantic run and both Linux full runs pass without retry; 50 fixed-role plus the explicit custom-role cases have semantic coverage, all adaptive/recovery journeys pass, reused numeric IDs never recover pre-session report drafts/recent robots, ordinary reload/offline retains current-session drafts, the ten canonical Linux visual baselines match, and every applicable WCAG A/AA axe violation count is zero.

- [ ] **Step 5: Commit acceptance coverage**

```bash
git add apps/web/e2e
git commit -m "test(web): cover roles themes devices and recovery"
```

---

### Task 5: Enforce bundle budgets, update product truth, and run the release gate

**Files:**

- Create: `apps/web/scripts/check-build-budget.mjs`
- Modify: `apps/web/package.json`
- Modify if the measured chunk budget fails: `apps/web/src/app/routing/AppRouter.tsx`
- Modify if the measured chunk budget fails: `apps/web/src/app/routing/AppRouter.test.tsx`
- Modify: `scripts/verify.sh`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `deploy/README.md`

**Interfaces:**

- Produces deterministic build budgets and one complete repository verification entry point.
- Documents canonical product routes, roles, themes, checks, and compatibility behavior.

- [ ] **Step 1: Add a failing build-budget gate**

Create `check-build-budget.mjs` to read `dist/assets`, gzip each `.js`/`.css` file with `gzipSync`, and enforce:

```js
const budgets = {
  maxJavaScriptChunkGzip: 180 * 1024,
  maxTotalJavaScriptGzip: 420 * 1024,
  maxTotalCssGzip: 90 * 1024,
}
```

Print each asset's raw and gzip bytes, then exit `1` with the exceeded budget names. Add `"check:build-budget": "node scripts/check-build-budget.mjs"`.

- [ ] **Step 2: Build and run the new gate**

Run:

```bash
cd apps/web
npm run build
npm run check:build-budget
```

Expected before optimization: either PASS within all three explicit budgets or FAIL naming the exact oversized chunk; a missing `dist` must always fail.

- [ ] **Step 3: Split only measured oversized chunks and update documentation**

If the gate identifies a chunk over 180 KiB gzip, first add failing `AppRouter.test.tsx` cases that exercise a lazy domain route through its accessible `LoadingState`, then resolve the canonical screen, and prove all `ROUTE_ELEMENTS` keys still equal the manifest IDs registered by the router. Introduce `React.lazy` at domain route boundaries in `AppRouter.tsx`, with one shared accessible `Suspense` fallback; isolate Leaflet in the robot-check route. Rerun the focused router test, full unit suite, build, and budget check. Do not add manual chunks or touch either router file when the initial measured gate already passes.

Update `README.md` with:

- canonical screen and role table;
- «Проверка робота» terminology and legacy `/emergency` redirect;
- legacy `/operator → /overview` behavior with meaningful `park` query preservation;
- system/light/dark theme behavior;
- desktop comfortable/compact density behavior and the forced-comfortable phone rule;
- local commands for unit, contrast, legacy, build-budget, and E2E checks;
- statement that map/learning/help placeholders are no longer navigation items;
- owner-supplied robot-photo policy and schematic fallback.

Update `deploy/README.md` with the health/readiness checks and the exact smoke URLs `/overview`, `/robots`, and `/robots/<VIN>/check` after authenticated deployment.

Append these web commands to `run_web` in `scripts/verify.sh` after build/tests:

```sh
npm run check:contrast
npm run check:legacy-ui
npm run check:build-budget
```

Keep Playwright in its GitHub Actions step because browser installation is a separate dependency. The CI full browser command is `npm run test:e2e:linux`; native `npm run test:e2e` remains an optional semantic developer loop and never updates or approves snapshots.

- [ ] **Step 4: Run the complete release gate**

Run from repository root:

```bash
./scripts/verify.sh api
./scripts/verify.sh web
./scripts/verify.sh docker
npm --prefix apps/web run test:e2e:linux
git diff --check
git status --short
```

Expected: API, web, Docker, Playwright, contrast, legacy, and bundle checks exit `0`; `git diff --check` has no output; `git status --short` lists only the intended documentation/script changes for this task.

- [ ] **Step 5: Commit the final quality gate**

```bash
git add apps/web/scripts/check-build-budget.mjs apps/web/package.json apps/web/src/app/routing/AppRouter.tsx apps/web/src/app/routing/AppRouter.test.tsx scripts/verify.sh .github/workflows/ci.yml README.md deploy/README.md
git commit -m "chore: enforce redesigned product release gates"
```

---

## Final Completion Gate

After Task 5, rerun from a clean working tree:

```bash
./scripts/verify.sh api
./scripts/verify.sh web
./scripts/verify.sh docker
npm --prefix apps/web run test:e2e:linux
```

Required evidence before merge:

- every command exits `0` with zero failed tests;
- Playwright reports no retries and zero applicable WCAG A/AA axe violations;
- all ten viewport/theme baselines match;
- role journeys prove the principal operation within two transitions;
- canonical deep links survive reload and legacy links redirect without losing meaningful context;
- `check-legacy-ui` proves the old shell, UI primitives, monolithic CSS, Unicode nav glyphs, and empty placeholder routes are absent;
- the hand-authored protected-route ID set exactly equals the shell-manifest ID set, with no duplicate IDs;
- light/dark/system preferences persist, a live OS scheme change updates `system`, and no theme flash appears before first contentful paint;
- desktop compact density persists, resolves to comfortable on phone, and restores on return to desktop without losing URL/form context;
- reduced-motion and 200% reflow pass on onboarding, administration, reports, analytics, robot detail, and report composer surfaces in addition to Overview and Work;
- skip-link, route-focus, mobile-breakpoint focus, and browser Back journeys preserve URL/form context;
- Work and Reports retain cached list/detail data only for transient offline, timeout, and server failures, recover in place on retry, and purge/suppress the current user's protected cache after `401`/`403` while leaving another user's namespace untouched;
- auth calls the single `clearProtectedBrowserStorage()` before login and on logout/refresh-401, purging every report-draft/recent-robots-v2 namespace so delete/recreate with the same numeric ID cannot cross accounts; successful reload and transient offline preserve the active session's draft/recents;
- an ambiguous report creation reuses its persisted idempotency key and produces exactly one report row across retry/reload;
- browser offline, robot offline, stale data, permissions, configuration, conflict, and server failures remain distinguishable;
- the private production repository contains no runtime database data, credentials, tokens, cookies, or owner-supplied images without confirmed rights.
