# Robopark Mobile Responsive Shell — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Адаптивный UI: на ПК без регрессий (левый сайдбар), на телефоне (≤900px) — bottom bar + sheet «Ещё», плюс правки контента (формы, KPI, tracker, safe-area).

**Architecture:** Один `AppShell` с dual layout через CSS + DOM для mobile chrome. `nav.ts` делит пункты на `primary` / `more`. Без новых роутов и без API.

**Tech Stack:** React 19 + Vite + react-router-dom 7, plain CSS (`index.css`), TypeScript.

**Base branch:** `feature/mobile-responsive` (from `main`).  
**Spec:** `docs/superpowers/specs/2026-08-24-robopark-mobile-responsive-design.md`

## Global Constraints

- Breakpoint mobile shell: `max-width: 900px`
- Desktop (≥901px): left sidebar unchanged visually
- Mobile bottom primary slots (in order): Dashboard, Tasks, Emergency, Reports, **Ещё** (opens sheet, not a route)
- Stubs (map / learning / help) never in bottom bar — only in sheet
- Brand «Робопарк Сервис»; Manrope + orange tokens; light/dark via existing `theme.ts`
- No PWA, no role-specific bottom sets yet, no API changes
- UI language: Russian (`ru.nav.more`, `ru.nav.close`)
- Web has **no** vitest — verify with `npm run build` + manual DevTools checklist

---

## File structure (target)

| File | Responsibility |
|------|----------------|
| `apps/web/src/nav.ts` | `PRIMARY_NAV_IDS`, `primaryNavItems()`, `moreNavItems()` |
| `apps/web/src/i18n/ru.ts` | `nav.more`, `nav.close` |
| `apps/web/src/components/AppShell.tsx` | Sidebar (desktop) + mobile bottom bar + more sheet |
| `apps/web/src/components/MoreSheet.tsx` | Presentational sheet (optional extract; OK inline in AppShell if <~80 lines) |
| `apps/web/src/components/tracker/TrackerWorkspace.tsx` | Mobile: list vs detail pane with back |
| `apps/web/src/index.css` | Mobile shell, bottom nav, sheet, forms/KPI/login/safe-area, hover guard |

---

### Task 1: Nav split helpers + i18n

**Files:**
- Modify: `apps/web/src/nav.ts`
- Modify: `apps/web/src/i18n/ru.ts`
- Create: `apps/web/scripts/check-nav.mjs` (tiny Node assert, no new deps)

**Interfaces:**
- Produces:
  - `export const PRIMARY_NAV_IDS: readonly NavId[] = ['dashboard', 'tasks', 'emergency', 'reports']`
  - `export function primaryNavItems(role: string): NavItem[]`
  - `export function moreNavItems(role: string): NavItem[]`
- Consumes: existing `navItemsForRole(role)`, `NavItem`, `NavId`

- [ ] **Step 1: Add i18n strings**

In `apps/web/src/i18n/ru.ts` inside `nav`:

```ts
more: 'Ещё',
close: 'Закрыть',
```

- [ ] **Step 2: Implement nav helpers**

Replace/extend `apps/web/src/nav.ts`:

```ts
import { ru } from './i18n/ru'

export type NavId =
  | 'dashboard'
  | 'tasks'
  | 'robot_search'
  | 'emergency'
  | 'map'
  | 'analytics'
  | 'reports'
  | 'learning'
  | 'help'

export type NavItem = {
  id: NavId
  path: string
  label: string
  stub?: boolean
}

const ALL_NAV_ITEMS: NavItem[] = [
  { id: 'dashboard', path: '/dashboard', label: ru.nav.dashboard },
  { id: 'tasks', path: '/tasks', label: ru.nav.tasks },
  { id: 'robot_search', path: '/robots/search', label: ru.nav.robot_search },
  { id: 'emergency', path: '/emergency', label: ru.nav.emergency },
  { id: 'map', path: '/map', label: ru.nav.map, stub: true },
  { id: 'analytics', path: '/analytics', label: ru.nav.analytics },
  { id: 'reports', path: '/reports', label: ru.nav.reports },
  { id: 'learning', path: '/learning', label: ru.nav.learning, stub: true },
  { id: 'help', path: '/help', label: ru.nav.help, stub: true },
]

export const PRIMARY_NAV_IDS: readonly NavId[] = [
  'dashboard',
  'tasks',
  'emergency',
  'reports',
] as const

export function navItemsForRole(_role: string): NavItem[] {
  return ALL_NAV_ITEMS
}

export function primaryNavItems(role: string): NavItem[] {
  const all = navItemsForRole(role)
  return PRIMARY_NAV_IDS.map((id) => all.find((item) => item.id === id)).filter(
    (item): item is NavItem => item != null,
  )
}

export function moreNavItems(role: string): NavItem[] {
  const primary = new Set<NavId>(PRIMARY_NAV_IDS)
  return navItemsForRole(role).filter((item) => !primary.has(item.id))
}
```

- [ ] **Step 3: Add Node check script**

Create `apps/web/scripts/check-nav.mjs`:

```js
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const src = readFileSync(join(root, 'src/nav.ts'), 'utf8')
const start = src.indexOf('PRIMARY_NAV_IDS')
if (start < 0) {
  console.error('PRIMARY_NAV_IDS missing')
  process.exit(1)
}
const block = src.slice(start, start + 280)
for (const id of ['dashboard', 'tasks', 'emergency', 'reports']) {
  if (!block.includes(`'${id}'`)) {
    console.error('PRIMARY_NAV_IDS missing', id)
    process.exit(1)
  }
}
if (!src.includes('function primaryNavItems') || !src.includes('function moreNavItems')) {
  console.error('missing primaryNavItems/moreNavItems')
  process.exit(1)
}
console.log('nav check ok')
```

- [ ] **Step 4: Run check**

Run: `node apps/web/scripts/check-nav.mjs`  
Expected: `nav check ok`

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/nav.ts apps/web/src/i18n/ru.ts apps/web/scripts/check-nav.mjs
git commit -m "feat(web): split primary vs more nav for mobile shell"
```

---

### Task 2: AppShell — bottom bar + more sheet

**Files:**
- Modify: `apps/web/src/components/AppShell.tsx`
- Create (optional): `apps/web/src/components/MoreSheet.tsx`

**Interfaces:**
- Consumes: `primaryNavItems`, `moreNavItems`, `ru.nav.more`, `ru.nav.close`, existing badge/`REPORTS_BADGE_REFRESH`
- Produces: DOM with classes `mobile-bottom-nav`, `mobile-more-sheet`, `mobile-more-backdrop`; state `moreOpen: boolean`

- [ ] **Step 1: Wire imports and state in AppShell**

At top of `AppShell.tsx` ensure:

```ts
import { moreNavItems, navItemsForRole, primaryNavItems } from '../nav'
```

Inside component:

```ts
const [moreOpen, setMoreOpen] = useState(false)
const primary = primaryNavItems(user.role)
const more = moreNavItems(user.role)
const items = navItemsForRole(user.role) // keep for desktop sidebar
```

- [ ] **Step 2: Escape + body scroll lock when sheet open**

```ts
useEffect(() => {
  if (!moreOpen) return
  const onKey = (event: KeyboardEvent) => {
    if (event.key === 'Escape') setMoreOpen(false)
  }
  document.addEventListener('keydown', onKey)
  const prev = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  return () => {
    document.removeEventListener('keydown', onKey)
    document.body.style.overflow = prev
  }
}, [moreOpen])
```

Close sheet on route change:

```ts
useEffect(() => {
  setMoreOpen(false)
}, [location.pathname])
```

- [ ] **Step 3: Replace topbar user `details` on mobile path with sheet opener**

Keep desktop `details` menu for theme/admin/logout **or** unify: recommend — keep desktop `details` as today; on mobile hide `.topbar-menu` via CSS and show user name as button that calls `setMoreOpen(true)`.

Markup addition after `</div>` of `app-main` content (still inside `.app-shell`):

```tsx
<nav className="mobile-bottom-nav" aria-label={ru.nav.brand}>
  {primary.map((item) => (
    <NavLink
      key={item.id}
      to={item.path}
      className={({ isActive }) =>
        isActive ? 'mobile-nav-item active' : 'mobile-nav-item'
      }
    >
      <span className="mobile-nav-label">{item.label}</span>
      {item.id === 'reports' && reportsBadge > 0 ? (
        <span className="nav-count">{reportsBadge}</span>
      ) : null}
    </NavLink>
  ))}
  <button
    type="button"
    className={moreOpen ? 'mobile-nav-item active' : 'mobile-nav-item'}
    aria-expanded={moreOpen}
    aria-controls="mobile-more-sheet"
    onClick={() => setMoreOpen((v) => !v)}
  >
    <span className="mobile-nav-label">{ru.nav.more}</span>
  </button>
</nav>

{moreOpen ? (
  <>
    <button
      type="button"
      className="mobile-more-backdrop"
      aria-label={ru.nav.close}
      onClick={() => setMoreOpen(false)}
    />
    <div
      id="mobile-more-sheet"
      className="mobile-more-sheet"
      role="dialog"
      aria-modal="true"
      aria-label={ru.nav.more}
    >
      <div className="mobile-more-head">
        <strong>{user.username}</strong>
        <span className="topbar-user-role">{roleLabel(user.role)}</span>
        <button type="button" className="btn-ghost" onClick={() => setMoreOpen(false)}>
          {ru.nav.close}
        </button>
      </div>
      <nav className="mobile-more-nav">
        {more.map((item) => (
          <NavLink
            key={item.id}
            to={item.path}
            className={({ isActive }) => (isActive ? 'nav-item active' : 'nav-item')}
            onClick={() => setMoreOpen(false)}
          >
            <span className="nav-item-label">{item.label}</span>
            {item.stub ? <span className="nav-soon">{ru.nav.soon}</span> : null}
          </NavLink>
        ))}
      </nav>
      <div className="mobile-more-actions">
        {showAdminLink ? (
          <Link className="topbar-menu-item" to="/admin" onClick={() => setMoreOpen(false)}>
            {ru.nav.admin}
          </Link>
        ) : null}
        <button type="button" className="topbar-menu-item" onClick={toggleTheme}>
          {theme === 'light' ? ru.theme.dark : ru.theme.light}
        </button>
        <button
          type="button"
          className="topbar-menu-item"
          onClick={() => {
            setMoreOpen(false)
            void logout()
          }}
        >
          {ru.signOut}
        </button>
      </div>
    </div>
  </>
) : null}
```

Also add on topbar user (mobile): optional button with class `mobile-user-open` calling `setMoreOpen(true)` — or rely on «Ещё» only (spec allows). Prefer button next to park that opens sheet:

```tsx
<button
  type="button"
  className="topbar-pill topbar-user mobile-user-open"
  onClick={() => setMoreOpen(true)}
>
  <span className="topbar-user-name">{user.username}</span>
  <span className="topbar-user-role">{roleLabel(user.role)}</span>
</button>
```

Keep existing `<details className="topbar-menu">` for desktop; CSS will show one or the other.

- [ ] **Step 4: Typecheck**

Run: `cd apps/web && npm run build`  
Expected: success (or only pre-existing unrelated errors — fix any from this task)

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/AppShell.tsx apps/web/src/components/MoreSheet.tsx
git commit -m "feat(web): mobile bottom nav and more sheet in AppShell"
```

---

### Task 3: CSS — mobile shell, safe-area, forms, KPI, hover

**Files:**
- Modify: `apps/web/src/index.css`

**Interfaces:**
- Produces CSS variables `--bottom-nav-height`, `--safe-bottom`; classes from Task 2

- [ ] **Step 1: Add tokens on `:root`**

```css
:root,
[data-theme='light'] {
  /* existing tokens... */
  --bottom-nav-height: 3.75rem;
  --safe-bottom: env(safe-area-inset-bottom, 0px);
}
```

- [ ] **Step 2: Guard hover lift for touch**

Find button hover `transform: translateY(-1px)` and wrap:

```css
@media (hover: hover) and (pointer: fine) {
  .btn:hover,
  button:hover:not(.topbar-menu-item):not(.tracker-item):not(.mobile-nav-item) {
    transform: translateY(-1px);
  }
}
```

Remove the old unconditional hover-transform rule to avoid double-definition.

- [ ] **Step 3: Replace/extend existing `@media (max-width: 900px)` app-shell block**

Replace the block that turns sidebar into top grid (~lines 968–981) with:

```css
@media (max-width: 900px) {
  .app-shell {
    grid-template-columns: 1fr;
    min-height: 100dvh;
  }

  .sidebar {
    display: none;
  }

  .topbar-menu {
    display: none;
  }

  .mobile-user-open {
    display: inline-flex;
  }

  .app-content {
    padding-bottom: calc(var(--bottom-nav-height) + var(--safe-bottom) + 0.75rem);
  }

  .mobile-bottom-nav {
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 40;
    min-height: calc(var(--bottom-nav-height) + var(--safe-bottom));
    padding: 0.35rem 0.25rem calc(0.35rem + var(--safe-bottom));
    border-top: 1px solid var(--border);
    background: var(--surface);
    box-shadow: 0 -4px 16px rgb(0 0 0 / 6%);
  }

  .mobile-nav-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 0.15rem;
    min-height: 2.75rem;
    padding: 0.35rem 0.2rem;
    border: 0;
    border-radius: 0.75rem;
    background: transparent;
    color: var(--text-muted);
    font-size: 0.68rem;
    font-weight: 600;
    text-decoration: none;
    position: relative;
  }

  .mobile-nav-item.active {
    color: var(--brand);
    background: var(--surface-muted);
  }

  .mobile-nav-label {
    text-align: center;
    line-height: 1.15;
  }

  .mobile-more-backdrop {
    position: fixed;
    inset: 0;
    z-index: 50;
    border: 0;
    background: rgb(0 0 0 / 45%);
  }

  .mobile-more-sheet {
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 51;
    max-height: min(85dvh, 36rem);
    overflow: auto;
    padding: 1rem 1rem calc(1rem + var(--safe-bottom));
    border-radius: 1rem 1rem 0 0;
    background: var(--surface);
    border-top: 1px solid var(--border);
    display: grid;
    gap: 0.75rem;
  }

  .mobile-more-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
  }

  .mobile-more-nav,
  .mobile-more-actions {
    display: grid;
    gap: 0.35rem;
  }

  .inline-form,
  .action-row,
  .actions {
    flex-direction: column;
    align-items: stretch;
  }

  .inline-form input,
  .inline-form select,
  .action-row input,
  .action-row select,
  .panel input,
  .panel select,
  .panel textarea,
  .panel button {
    width: 100%;
    min-width: 0;
    min-height: 2.75rem;
  }

  .page {
    padding: 0.85rem;
  }

  .panel {
    padding: 1rem;
  }

  .stat-grid,
  .dashboard-kpi-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .login-card,
  .cabinet {
    margin-top: 1.5rem;
    padding: 1.25rem;
  }

  .table-scroll,
  .admin-table-wrap {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }
}

.mobile-bottom-nav,
.mobile-user-open,
.mobile-more-backdrop,
.mobile-more-sheet {
  display: none;
}

@media (max-width: 480px) {
  .stat-grid,
  .dashboard-kpi-grid {
    grid-template-columns: 1fr;
  }

  .mobile-nav-label {
    font-size: 0.62rem;
  }
}
```

**Important:** Place the desktop `display: none` defaults for mobile-only chrome **before** the `@media (max-width: 900px)` block that sets `display: grid` on `.mobile-bottom-nav`, or use:

```css
.mobile-bottom-nav { display: none; }
.mobile-user-open { display: none; }
@media (max-width: 900px) {
  .mobile-bottom-nav { display: grid; }
  .mobile-user-open { display: inline-flex; }
}
```

Do **not** leave the old rule that makes `.sidebar-nav` a multi-column grid on mobile.

Keep existing dashboard `@media (max-width: 900px)` grid-areas block.

- [ ] **Step 4: Build**

Run: `cd apps/web && npm run build`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/index.css
git commit -m "feat(web): mobile shell CSS with bottom nav and safe-area"
```

---

### Task 4: Tracker workspace — mobile list / detail

**Files:**
- Modify: `apps/web/src/components/tracker/TrackerWorkspace.tsx`
- Modify: `apps/web/src/index.css` (tracker mobile rules)
- Modify: `apps/web/src/i18n/ru.ts` if needed (`ru.back` already exists)

**Interfaces:**
- Consumes: existing workspace state `selected` / `detail`
- Produces: class `tracker-grid` + modifier `tracker-grid--detail` when `selected` set; back button clears selection on mobile

- [ ] **Step 1: Add back control + className**

```tsx
return (
  <section
    className={
      selected ? 'tracker-grid tracker-grid--has-detail' : 'tracker-grid'
    }
  >
    <div className="tracker-list-pane">
      <IssueFilters allowUntagged={allowUntagged} onApply={loadIssues} />
      {error && <p className="error">{error}</p>}
      <IssueList items={items} onSelect={(key) => void openIssue(key)} selected={selected} />
    </div>
    <div className="tracker-detail-pane">
      {selected ? (
        <button
          type="button"
          className="page-back tracker-detail-back"
          onClick={() => {
            setSelected('')
            setDetail(null)
            setComments([])
            setTransitions([])
          }}
        >
          {ru.back}
        </button>
      ) : null}
      <IssueDetailPanel comments={comments} issue={detail} />
      {detail && (
        <IssueActionsPanel
          canWrite={canWrite}
          /* existing handlers */
          transitions={transitions}
        />
      )}
    </div>
  </section>
)
```

Import `ru` from `../../i18n/ru`. Preserve any newer props (`defaultQueue` / `defaultPark`) if present on the branch — merge carefully.

- [ ] **Step 2: CSS for panes**

```css
.tracker-list-pane,
.tracker-detail-pane {
  display: grid;
  gap: 0.75rem;
}

.tracker-detail-back {
  display: none;
}

@media (max-width: 900px) {
  .tracker-grid--has-detail .tracker-list-pane {
    display: none;
  }

  .tracker-grid:not(.tracker-grid--has-detail) .tracker-detail-pane {
    display: none;
  }

  .tracker-detail-back {
    display: inline-flex;
  }
}
```

- [ ] **Step 3: Build**

Run: `cd apps/web && npm run build`  
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/components/tracker/TrackerWorkspace.tsx apps/web/src/index.css apps/web/src/i18n/ru.ts
git commit -m "feat(web): mobile tracker list/detail panes with back"
```

---

### Task 5: Admin overflow wrappers + manual QA

**Files:**
- Modify: `apps/web/src/pages/Admin.tsx` (wrap wide tables/lists in `<div className="table-scroll">` where overflow is likely)
- Modify: other pages only if an obvious fixed-width row remains (Operator blockers list is already card-like — skip if fine)

- [ ] **Step 1: Wrap admin dense lists**

Around any wide `<table>` or multi-column admin grids that overflow, wrap:

```tsx
<div className="table-scroll">{/* existing table or row */}</div>
```

If Admin has no `<table>`, wrap the parks/mechanics lists that use horizontal `inline-form` rows — CSS from Task 3 already stacks `.inline-form` on mobile; only add scroll wrapper if a specific block still overflows in DevTools.

- [ ] **Step 2: Manual checklist (DevTools)**

1. Width 1280: sidebar visible; no bottom nav; desktop user menu works.
2. Width 390: sidebar hidden; bottom nav 5 slots; content not under bar; Reports badge if count > 0.
3. Open «Ещё»: sheet + backdrop; Escape closes; theme + logout work; admin link for royal.
4. Dashboard KPI readable; forms full-width.
5. Tracker: select issue → detail only + Назад → list.
6. Login at 375 width usable.
7. Toggle light/dark on mobile.

- [ ] **Step 3: Final build**

Run: `cd apps/web && npm run build && node scripts/check-nav.mjs`  
Expected: both OK

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/pages/Admin.tsx apps/web/src/index.css
git commit -m "fix(web): mobile overflow polish for admin lists"
```

---

## Spec coverage (self-review)

| Spec requirement | Task |
|---|---|
| Desktop sidebar unchanged ≥901 | Task 3 (sidebar not `display:none` outside mobile MQ) |
| Mobile hide sidebar + bottom bar + Ещё | Tasks 2–3 |
| Primary 4 + sheet secondary / admin / theme / logout | Tasks 1–2 |
| Reports badge on bottom | Task 2 |
| Forms / KPI / safe-area / login padding | Task 3 |
| Tracker list/detail back | Task 4 |
| Admin horizontal overflow | Task 5 |
| No PWA / no API | Global constraints |
| Hover lift only on fine pointer | Task 3 |

Placeholder scan: none intentional. Nav helper names consistent: `primaryNavItems` / `moreNavItems` / `PRIMARY_NAV_IDS`.
