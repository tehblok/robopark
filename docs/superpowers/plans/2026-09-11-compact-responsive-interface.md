# Compact Responsive Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Сделать все доступные ролям экраны Robopark компактными, понятными и полноценными на телефоне без потери функций на ПК.

**Architecture:** Вынести плотность, адаптивное сворачивание и иерархию действий в общие React-примитивы. Переводить домены последовательно, не меняя API, URL и ролевую модель. Каждый этап закрывается компонентными и браузерными тестами.

**Tech Stack:** React 19, TypeScript, React Router, CSS container/media queries, Vitest, Testing Library, Playwright, axe, Vite.

**Spec:** `docs/superpowers/specs/2026-09-11-compact-responsive-interface-design.md`

## Global Constraints

- Не добавлять новый UI-framework или отдельную мобильную версию.
- Сохранить API, схему БД, ролевую модель, публичные URL, фильтры и черновики.
- Минимальный touch-target на 320–390 px — 44×44 px; размер мобильных form controls — не менее 16 px.
- На каждом экране не более одной primary-кнопки; опасные действия требуют подтверждения.
- Чужая задача читаема, но механик не видит мутаций и проверки робота до взятия/перехвата.
- Все изменения Tracker остаются от имени настроенного бота.
- Эталонные снимки менять только после визуальной проверки actual-изображений.

---

### Task 1: Общие примитивы плотности и раскрытия

**Files:**
- Create: `apps/web/src/design-system/layout/ResponsiveDisclosure.tsx`
- Create: `apps/web/src/design-system/layout/ResponsiveDisclosure.css`
- Create: `apps/web/src/design-system/layout/ResponsiveDisclosure.test.tsx`
- Modify: `apps/web/src/design-system/layout/PageLayout.tsx`
- Modify: `apps/web/src/design-system/layout/PageLayout.css`
- Test: `apps/web/src/design-system/layout/PageLayout.test.tsx`

**Interfaces:**
- Produces: `PanelProps.density?: 'summary' | 'work' | 'dense'`.
- Produces: `ResponsiveDisclosureGroup({ children, initialOpenId, label })` and `ResponsiveDisclosure({ id, title, summary, children })`.
- Produces: on widths below 600 px only one disclosure content is mounted; at 600 px and above all sections are visible.

- [ ] **Step 1: Write failing primitive tests**

```tsx
it('uses the saved fallback when no collapse preference exists', () => {
  render(<Panel collapsible defaultCollapsed storageKey="secondary" title="Secondary">Body</Panel>)
  expect(screen.queryByText('Body')).not.toBeInTheDocument()
})

it('opens only one disclosure on a phone', async () => {
  matchMediaWidth(390)
  render(<ResponsiveDisclosureGroup label="Actions"><ResponsiveDisclosure id="a" title="A">Alpha</ResponsiveDisclosure><ResponsiveDisclosure id="b" title="B">Beta</ResponsiveDisclosure></ResponsiveDisclosureGroup>)
  await user.click(screen.getByRole('button', { name: 'A' }))
  await user.click(screen.getByRole('button', { name: 'B' }))
  expect(screen.queryByText('Alpha')).not.toBeInTheDocument()
  expect(screen.getByText('Beta')).toBeVisible()
})
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `cd apps/web && npm test -- --run src/design-system/layout/PageLayout.test.tsx src/design-system/layout/ResponsiveDisclosure.test.tsx`

Expected: FAIL because the fallback is ignored when storage returns `null`, and `ResponsiveDisclosure` does not exist.

- [ ] **Step 3: Implement the primitives**

```ts
function readCollapsed(storageKey: string | undefined, fallback: boolean): boolean {
  if (!storageKey || typeof window === 'undefined') return fallback
  const stored = window.localStorage.getItem(panelStorageKey(storageKey))
  return stored === null ? fallback : stored === '1'
}
```

Add `data-density={density}` to `Panel`. Implement `ResponsiveDisclosureGroup` with `matchMedia('(max-width: 599px)')`, an `openId` state and buttons carrying `aria-expanded`/`aria-controls`; render every child on desktop and only `openId` content on phone. Preserve a user's selection while resizing.

- [ ] **Step 4: Run focused tests**

Run: `cd apps/web && npm test -- --run src/design-system/layout/PageLayout.test.tsx src/design-system/layout/ResponsiveDisclosure.test.tsx`

Expected: all selected tests PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/design-system/layout
git commit -m "feat: add compact responsive layout primitives"
```

### Task 2: Компактная задача и безопасное владение

**Files:**
- Modify: `apps/web/src/domains/work/IssueWorkbench.tsx`
- Modify: `apps/web/src/domains/work/work.css`
- Modify: `apps/web/src/components/tracker/IssueActionsPanel.tsx`
- Modify: `apps/web/src/components/tracker/task-card.css`
- Test: `apps/web/src/domains/work/IssueWorkbench.test.tsx`
- Test: `apps/web/src/components/tracker/IssueActionsPanel.test.tsx`
- Test: `apps/web/e2e/operational/work.spec.ts`
- Test: `apps/web/e2e/operational/task-card.spec.ts`
- Test: `apps/web/e2e/operational/fixtures.ts`

**Interfaces:**
- Consumes: `Panel density`, `ResponsiveDisclosureGroup`, `ResponsiveDisclosure` from Task 1.
- Produces: action section IDs `comment`, `parts`, `transition`, `assignee`, `handoff`, `history`.
- Produces: `mechanicCanWork` as the single UI gate for comments, attachments, status, assignment, parts and embedded robot check.

- [ ] **Step 1: Add failing ownership and compact-flow tests**

```tsx
expect(screen.getByText(/Для изменений возьмите задачу вместо сменщика/)).toBeVisible()
expect(screen.queryByRole('tab', { name: 'Проверка робота' })).not.toBeInTheDocument()
expect(screen.queryByRole('button', { name: 'Закрыть тикет' })).not.toBeInTheDocument()
```

Add a 390 px component/browser assertion that the task summary and comment action are visible before the `parts`, `transition`, `assignee`, `handoff` and full `history` contents are expanded.

- [ ] **Step 2: Verify focused failure**

Run: `cd apps/web && npm test -- --run src/domains/work/IssueWorkbench.test.tsx src/components/tracker/IssueActionsPanel.test.tsx`

Expected: the foreign-task test or compact disclosure assertions FAIL before implementation.

- [ ] **Step 3: Implement the task hierarchy**

Keep the task identity, status, owner, latest significant comment and comment/photo composer visible. Put parts, transitions, assignee, handoff and full history into named responsive disclosures. Use:

```ts
const mechanicCanWork = Boolean(
  detail.data && (user.role !== 'mechanic' || mechanicOwnsIssue(user, detail.data)),
)
```

Gate every mutation and the `check` tab with this value. A direct `detailTab=check` URL for a non-owner resolves to `task` without requesting Emergency data.

- [ ] **Step 4: Verify the work domain**

Run: `cd apps/web && npm test -- --run src/domains/work/IssueWorkbench.test.tsx src/components/tracker/IssueActionsPanel.test.tsx && npx playwright test e2e/operational/work.spec.ts e2e/operational/task-card.spec.ts e2e/operational/task-collaboration.spec.ts e2e/operational/work-tabs.spec.ts`

Expected: all selected tests PASS at desktop and phone widths.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/domains/work apps/web/src/components/tracker apps/web/e2e/operational
git commit -m "feat: simplify task work on mobile"
```

### Task 3: Пересборка проверки робота

**Files:**
- Create: `apps/web/src/domains/robots/RobotCheckNavigation.tsx`
- Test: `apps/web/src/domains/robots/RobotCheckNavigation.test.tsx`
- Modify: `apps/web/src/domains/robots/RobotCheckSummary.tsx`
- Modify: `apps/web/src/domains/robots/RobotCheckWorkspace.tsx`
- Modify: `apps/web/src/domains/robots/RobotCheckTabs.tsx`
- Modify: `apps/web/src/domains/robots/robot-check.css`
- Test: `apps/web/src/domains/robots/RobotCheckWorkspace.test.tsx`
- Test: `apps/web/src/domains/robots/RobotCheckTabs.test.tsx`
- Test: `apps/web/e2e/operational/robots.spec.ts`
- Test: `apps/web/e2e/operational/diagnostic-markers.spec.ts`

**Interfaces:**
- Produces: `PRIMARY_CHECK_TAB_IDS = ['state', 'errors', 'scheme'] as const`.
- Produces: `RobotCheckNavigation({ tabs, activeId, onChange })`; secondary selection remains a real tab and calls existing `onChange(id)`.
- Preserves: `activeTab` URL values and `RobotCheckWorkspaceProps`.

- [ ] **Step 1: Write failing navigation and summary tests**

```tsx
expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual(['Состояние', 'Ошибки', 'Схема'])
expect(screen.getByRole('button', { name: 'Ещё' })).toBeVisible()
await user.click(screen.getByRole('button', { name: 'Показать неисправность' }))
expect(onChange).toHaveBeenCalledWith('scheme')
```

Also assert that the summary exposes robot number, connection, charge, freshness and the leading diagnostic message, while VIN and coordinates are supplementary details.

- [ ] **Step 2: Verify focused failure**

Run: `cd apps/web && npm test -- --run src/domains/robots/RobotCheckNavigation.test.tsx src/domains/robots/RobotCheckWorkspace.test.tsx src/domains/robots/RobotCheckTabs.test.tsx`

Expected: FAIL because all technical tabs are currently in one horizontal row and the compact leading action is absent.

- [ ] **Step 3: Implement summary and navigation**

Partition tabs without changing their IDs:

```ts
const primary = tabs.filter(tab => PRIMARY_CHECK_TAB_IDS.includes(tab.id as typeof PRIMARY_CHECK_TAB_IDS[number]))
const secondary = tabs.filter(tab => !PRIMARY_CHECK_TAB_IDS.includes(tab.id as typeof PRIMARY_CHECK_TAB_IDS[number]))
const visible = secondary.some(tab => tab.id === activeId)
  ? [...primary, secondary.find(tab => tab.id === activeId)!]
  : primary
```

Render `visible` with correct tab keyboard semantics and expose `secondary` from the `Ещё` menu. The leading error button selects the diagnostic event and calls `onTabChange('scheme')`. On widths below 600 px render summary above content, not as a separate master-detail column.

- [ ] **Step 4: Verify robot flows**

Run: `cd apps/web && npm test -- --run src/domains/robots && npx playwright test e2e/operational/robots.spec.ts e2e/operational/diagnostic-markers.spec.ts e2e/operational/robot-qr.spec.ts`

Expected: all selected tests PASS; no permanent wheel circles; URL and six image views remain correct.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/domains/robots apps/web/e2e/operational
git commit -m "feat: simplify robot diagnostics"
```

### Task 4: Плотные списки склада, репортов и кампаний

**Files:**
- Modify: `apps/web/src/domains/inventory/InventoryPage.tsx`
- Modify: `apps/web/src/domains/inventory/inventory.css`
- Test: `apps/web/src/domains/inventory/InventoryPage.test.tsx`
- Modify: `apps/web/src/pages/Reports.tsx`
- Modify: `apps/web/src/components/reports/ReportDetail.tsx`
- Modify: `apps/web/src/components/reports/ReportList.tsx`
- Test: `apps/web/src/pages/Reports.test.tsx`
- Modify: `apps/web/src/domains/campaigns/CampaignsPage.tsx`
- Modify: `apps/web/src/domains/campaigns/campaigns.css`
- Test: `apps/web/src/domains/campaigns/CampaignsPage.test.tsx`
- Test: `apps/web/e2e/operational/admin-reports.spec.ts`

**Interfaces:**
- Consumes: responsive disclosures and dense panels from Task 1.
- Preserves: inventory, report and campaign API payloads and routes.

- [ ] **Step 1: Add failing mobile hierarchy tests**

For each domain at 390 px assert that search/filter and list summary are visible; create/edit/history/metrics forms are absent until their named disclosure or selected detail is opened. Assert that inventory thumbnails remain square and at most 72 px:

```tsx
render(renderInventoryPage(park))
await screen.findByRole('heading', { name: 'Подвязка' })
expect(screen.getByRole('combobox', { name: 'Компонента' })).toBeVisible()
expect(screen.queryByRole('textbox', { name: 'Название новой запчасти' })).not.toBeInTheDocument()
await userEvent.click(screen.getByRole('button', { name: 'Добавить запчасть' }))
expect(screen.getByRole('textbox', { name: 'Название новой запчасти' })).toBeVisible()
```

- [ ] **Step 2: Verify failure**

Run: `cd apps/web && npm test -- --run src/domains/inventory/InventoryPage.test.tsx src/pages/Reports.test.tsx src/domains/campaigns/CampaignsPage.test.tsx`

Expected: at least one compact-layout assertion FAILS with the current simultaneously expanded forms.

- [ ] **Step 3: Apply the common hierarchy**

Use dense list cards with visible identity/status/park/deadline. Move inventory create/edit/movement/printing, report history/attachments and campaign create/metrics/completion into responsive disclosures. Keep list/detail navigation exclusive below 900 px and preserve the existing `MasterDetail` return behavior.

- [ ] **Step 4: Verify the domains**

Run: `cd apps/web && npm test -- --run src/domains/inventory src/pages/Reports.test.tsx src/components/reports src/domains/campaigns && npx playwright test e2e/operational/admin-reports.spec.ts e2e/operational/report-photo-drafts.spec.ts`

Expected: all selected tests PASS and drafts/photos survive reload.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/domains/inventory apps/web/src/domains/campaigns apps/web/src/components/reports apps/web/src/pages/Reports.tsx apps/web/e2e/operational
git commit -m "feat: compact data-heavy workflows"
```

### Task 5: Обзор, аналитика и администрирование

**Files:**
- Modify: `apps/web/src/domains/shift/OverviewPage.tsx`
- Modify: `apps/web/src/domains/shift/OverviewSections.tsx`
- Modify: `apps/web/src/domains/shift/overview.css`
- Modify: `apps/web/src/domains/analytics/AnalyticsWorkspace.tsx`
- Modify: `apps/web/src/domains/analytics/analytics.css`
- Modify: `apps/web/src/pages/Admin.tsx`
- Modify: `apps/web/src/pages/AdminEmergencyConfig.tsx`
- Modify: `apps/web/src/pages/AdminTrackerWorkspace.tsx`
- Create: `apps/web/src/pages/AdminEmergencyConfig.test.tsx`
- Create: `apps/web/src/pages/AdminTrackerWorkspace.test.tsx`
- Modify: `apps/web/src/components/admin/AdminUsersPanel.tsx`
- Modify: `apps/web/src/components/admin/AdminRolesPanel.tsx`
- Test: `apps/web/src/domains/shift/OverviewPage.test.tsx`
- Test: `apps/web/src/domains/analytics/AnalyticsWorkspace.test.tsx`
- Test: `apps/web/src/pages/Admin.test.tsx`
- Test: `apps/web/src/components/admin/AdminUsersPanel.test.tsx`
- Create: `apps/web/src/components/admin/AdminRolesPanel.test.tsx`
- Test: `apps/web/e2e/operational/analytics.spec.ts`
- Test: `apps/web/e2e/operational/admin-settings.spec.ts`

**Interfaces:**
- Consumes: `Panel density` and responsive disclosures.
- Preserves: current permission gates, park multi-select contracts and durable OTA progress.

- [ ] **Step 1: Write failing priority/layout tests**

At 390 px assert overview order `status -> critical queue -> next action -> secondary KPI`; analytics park selection occupies one row; admin list/search appears before any editor; integration secrets are never included in collapsed summaries:

```tsx
const headings = screen.getAllByRole('heading').map(node => node.textContent)
expect(headings.indexOf('Очередь внимания')).toBeLessThan(headings.indexOf('Поток задач: пришло / ушло'))
expect(screen.queryByText(/Session_id|tracker_token/)).not.toBeInTheDocument()
```

- [ ] **Step 2: Verify failure**

Run: `cd apps/web && npm test -- --run src/domains/shift src/domains/analytics src/pages/Admin.test.tsx src/components/admin`

Expected: current ordering or simultaneous editor visibility causes a focused failure.

- [ ] **Step 3: Apply density and progressive disclosure**

Reorder existing data without changing requests. Use dense panels for KPI, collapse secondary charts below the queue on phones, and make admin editors explicit selected-entity disclosures/drawers. Keep dangerous host operations in their own collapsed panel with their existing confirmations and progress.

- [ ] **Step 4: Verify management and insight screens**

Run: `cd apps/web && npm test -- --run src/domains/shift src/domains/analytics src/pages/Admin.test.tsx src/pages/AdminEmergencyConfig.test.tsx src/pages/AdminTrackerWorkspace.test.tsx src/components/admin && npx playwright test e2e/operational/overview.spec.ts e2e/operational/analytics.spec.ts e2e/operational/admin-settings.spec.ts e2e/operational/ops.spec.ts`

Expected: all selected tests PASS for royal/admin/operator/mechanic/driver fixtures.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/domains/shift apps/web/src/domains/analytics apps/web/src/pages apps/web/src/components/admin apps/web/e2e/operational
git commit -m "feat: compact overview and management screens"
```

### Task 6: Полная матрица маршрутов, ролей и адаптивности

**Files:**
- Create: `apps/web/e2e/operational/routeFixtures.ts`
- Create: `apps/web/e2e/operational/route-role-layout.spec.ts`
- Modify: `apps/web/e2e/operational/responsive-visual.spec.ts`
- Modify: `apps/web/e2e/operational/responsive-visual.spec.ts-snapshots/*.png`
- Modify: `apps/web/e2e/support/assertA11y.test.ts`
- Modify: `apps/web/scripts/check-nav.mjs`

**Interfaces:**
- Consumes: `ROUTE_MANIFEST` as the only route inventory and `userForRole()` as the role fixture source.
- Produces: `fixturePath(route: RouteManifestItem): string`, resolving dynamic routes to `/work/ROBOPARK-42`, `/robots/YASADR00000000447`, `/robots/YASADR00000000447/check`, `/campaigns/4` and `/reports/1`.
- Produces: `openRouteFixture(page: Page, routeId: AppRouteId, user: User): Promise<void>`.
- Produces: a generated test matrix that skips denied routes and tests every accessible route at 320, 390, 768, 1024 and 1440 px.

- [ ] **Step 1: Add a failing manifest completeness test**

```ts
for (const role of roles) for (const route of ROUTE_MANIFEST.filter(item => item.surface === 'shell')) {
  test(`${role}: ${route.id}`, async ({ page }) => {
    const user = userForRole(role)
    if (!canAccessRoute(user, route.id)) return
    await openRouteFixture(page, route.id, user)
    await assertResponsiveContracts(page, 390)
    await assertNoSeriousA11yViolations(page)
  })
}
```

- [ ] **Step 2: Run and observe uncovered routes**

Run: `cd apps/web && npx playwright test e2e/operational/route-role-layout.spec.ts`

Expected: FAIL for routes without a deterministic fixture or compact contract.

- [ ] **Step 3: Complete deterministic route fixtures and visual states**

Add exact fixtures for every accessible route; do not hide errors with broad waits or screenshot thresholds. Keep visual snapshots for the main representative screens and semantic layout assertions for the complete matrix. Resolve dynamic paths centrally:

```ts
export function fixturePath(route: RouteManifestItem): string {
  if (route.id === 'work-issue') return '/work/ROBOPARK-42?park=7'
  if (route.id === 'robot-detail') return '/robots/YASADR00000000447?park=7'
  if (route.id === 'robot-check') return '/robots/YASADR00000000447/check?park=7&tab=state'
  if (route.id === 'campaign-detail') return '/campaigns/4?park=7'
  if (route.id === 'report-detail') return '/reports/1?park=7'
  return `${route.path}${route.surface === 'shell' ? '?park=7' : ''}`
}

export async function openRouteFixture(page: Page, routeId: AppRouteId, user: User): Promise<void> {
  const route = ROUTE_MANIFEST.find(item => item.id === routeId)
  if (!route) throw new Error(`Unknown route fixture: ${routeId}`)
  await installOperational(page, { user })
  await page.goto(fixturePath(route))
  await expect(page.locator('main')).toBeVisible()
}
```

- [ ] **Step 4: Run all web checks**

Run: `cd apps/web && npm test -- --run && npm run lint && npm run check-nav && npm run build && npx playwright test`

Expected: all Vitest files and all Playwright tests PASS; lint may report only the explicitly reviewed existing warnings; production build succeeds.

- [ ] **Step 5: Commit**

```bash
git add apps/web
git commit -m "test: cover every role and responsive route"
```

### Task 7: Версия, полная регрессия и OTA

**Files:**
- Modify: `VERSION`
- Modify: `apps/api/pyproject.toml`
- Modify: `apps/api/uv.lock`
- Modify: `apps/api/src/robopark_api/services/ops/context.py`
- Modify: `apps/web/package.json`
- Modify: `apps/web/package-lock.json`
- Modify: `deploy/release-metadata.json`

**Interfaces:**
- Produces: version `0.1.30` in every version source and signed archive `robopark-release-0.1.30.zip`.
- Preserves: migration head `0025_local_task_claims` and compatibility from heads `0022_tracker_collaboration`, `0024_inventory`.

- [ ] **Step 1: Set the final patch version and notes**

Set `0.1.30` in `VERSION`, both package manifests/locks and `APP_VERSION`; describe the compact interface, simplified robot check and protected mechanic ownership in `update_notes`.

- [ ] **Step 2: Verify release consistency and all server suites**

Run: `python3 scripts/check-release-version.py`

Run: `cd apps/api && uv run pytest -q`

Run: `pytest -q tests/host`

Run: `./scripts/verify.sh api`

Expected: version check, all API tests, all host tests and static API verification PASS.

- [ ] **Step 3: Build, sign and inspect the OTA**

Run:

```bash
rp_version=0.1.30
rp_sha=$(git rev-parse --short HEAD)
rp_artifact_dir="artifacts/robopark-${rp_version}-${rp_sha}"
mkdir -p "$rp_artifact_dir"
ROBOPARK_SIGNING_KEY_FILE=/Users/tehblokdan/Desktop/Проекты/robopark/.worktrees/armbian-installer-ota/.release-secrets/release-signing.pem ./scripts/pack-release.sh "$rp_artifact_dir/robopark-release-${rp_version}.zip"
python3 scripts/verify-artifact.py --public-key /Users/tehblokdan/Desktop/Проекты/robopark/.worktrees/armbian-installer-ota/.release-secrets/release-public-key.pem "$rp_artifact_dir/robopark-release-${rp_version}.zip"
shasum -a 256 "$rp_artifact_dir/robopark-release-${rp_version}.zip"
```

Expected: archive inspection and signature verification PASS; no secret content appears in output.

- [ ] **Step 4: Install on the live host and repeat role workflows**

Upload the signed archive through the royal update screen. Wait for job state `succeeded`, host health `current_healthy`, and the new version in `/api/health/details`. Repeat: mechanic claim and takeover on `SDCFLEETOPS-375118`, owner comment, non-owner read-only view, robot `a100` check, operator report return/complete, royal report visibility, inventory access gates and 320/390 px visual inspection.

Expected: all operations succeed, Tracker comments are authored by `robot-service`, and no unavailable action is shown.

- [ ] **Step 5: Commit and push release sources**

```bash
git add VERSION apps/api/pyproject.toml apps/api/uv.lock apps/api/src/robopark_api/services/ops/context.py apps/web/package.json apps/web/package-lock.json deploy/release-metadata.json
git commit -m "release: prepare compact interface OTA"
git push origin main
```

- [ ] **Step 6: Record final evidence**

Report commit, installed version, OTA path, SHA-256, API/web/host test counts, live update job result and any non-blocking known warnings. Remove only the exact temporary QA cookie directory; retain externally created QA users/reports/comments unless the user separately authorizes deletion.
