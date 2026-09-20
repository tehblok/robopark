# Interface Geometry Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Привести Classic и «Новый A» к единой сетке, симметрии вкладок, отступов и скруглений на телефоне и ПК без изменения бизнес-логики.

**Architecture:** Общая геометрия задаётся семантическими CSS-токенами и базовыми design-system компонентами. Classic и «Новый A» сохраняют свою компоновку, но используют одинаковые поля, радиусы, высоты управлений и правила вложенных рамок. Проверка выполняется отдельно для двух режимов, тем и ключевых ширин.

**Tech Stack:** React 19, TypeScript, CSS custom properties, Vitest + Testing Library, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-20-interface-geometry-normalization-design.md`

## Global Constraints

- Оба режима — Classic и «Новый A» — входят в объём полность.
- Поля рабочей области: 12 px на телефоне, 24 px на планшете/ПК.
- Внутренний отступ карточки: 16 px на телефоне, 20 px на планшете/ПК.
- Радиусы: 8 px управления, 12 px карточки, 16 px sheet/диалоги.
- Минимальная высота интерактивного управления: 44 px.
- Бизнес-логика, URL-состояние, роли, API и форматы данных не меняются.
- Не добавлять зависимости; не перестраивать маршруты и доменные компоненты без необходимости.

## Review Focus

- Ширина 320 px и длинные русские подписи: нет горизонтальной прокрутки, вкладки не перекрываются.
- Переключение Classic/A с набранным комментарием и фото: черновик и файл не теряются.
- Длинная страница с нижней навигацией: последнее действие полностью прокручивается над панелью.
- Текстовый zoom 200% и клавиатура: фокус виден, а главное действие остаётся доступным.
- Границы 599/600, 899/900 и 1199/1200 px: компоновка не скачет в промежуточное положение и не создаёт наложений.

---

### Task 1: Единые токены и базовая геометрия

**Files:**
- Modify: `apps/web/src/app/interface/interfaceTokens.css`
- Modify: `apps/web/src/design-system/styles/tokens.css`
- Modify: `apps/web/src/design-system/layout/PageLayout.css`
- Modify: `apps/web/src/design-system/layout/MasterDetail.css`
- Modify: `apps/web/src/design-system/forms/FormField.css`
- Modify: `apps/web/src/design-system/actions/Button.css`
- Modify: `apps/web/src/design-system/overlays/overlays.css`
- Test: `apps/web/src/design-system/layout/PageLayout.test.tsx`
- Test: `apps/web/src/design-system/layout/MasterDetail.test.tsx`
- Test: `apps/web/src/design-system/forms/FormField.test.tsx`
- Test: `apps/web/src/design-system/actions/Button.test.tsx`

**Interfaces:**
- Consumes: existing `--rp-space-*`, `--rp-control-min-size`, density and color tokens.
- Produces: `--rp-page-gutter`, `--rp-section-gap`, `--rp-card-padding`, `--rp-form-gap`, `--rp-radius-control`, `--rp-radius-card`, `--rp-radius-sheet` for all later tasks.

- [ ] **Step 1: Write failing token and primitive tests**

Add source-contract assertions:

```ts
const tokens = readFileSync(resolve('src/app/interface/interfaceTokens.css'), 'utf8')
expect(tokens).toContain('--rp-page-gutter: 24px')
expect(tokens).toContain('--rp-radius-control: 8px')
expect(tokens).toContain('--rp-radius-card: 12px')
expect(tokens).toContain('--rp-radius-sheet: 16px')
expect(tokens).toMatch(/max-width: 599px[\s\S]*--rp-page-gutter: 12px/)
```

Keep the existing assertion that `PageLayout` itself does not own shell gutters.

- [ ] **Step 2: Run tests and confirm the new contract fails**

Run: `npm test -- --run src/design-system/layout/PageLayout.test.tsx src/design-system/layout/MasterDetail.test.tsx src/design-system/forms/FormField.test.tsx src/design-system/actions/Button.test.tsx`

Expected: FAIL because the geometry variables do not yet exist.

- [ ] **Step 3: Define tokens and migrate primitives**

Implement this contract in `interfaceTokens.css`:

```css
:root {
  --rp-page-gutter: 24px;
  --rp-section-gap: 24px;
  --rp-card-padding: 20px;
  --rp-form-gap: 16px;
  --rp-radius-control: 8px;
  --rp-radius-card: 12px;
  --rp-radius-sheet: 16px;
  --rp-shell-mobile-nav-height: 72px;
}

@media (max-width: 599px) {
  :root {
    --rp-page-gutter: 12px;
    --rp-section-gap: 20px;
    --rp-card-padding: 16px;
    --rp-form-gap: 12px;
  }
}
```

Map `Panel`, cards, form rows, buttons, master/detail panes and overlays to those semantic variables. Do not add page padding to `PageLayout`; shells consume `--rp-page-gutter`.

- [ ] **Step 4: Run primitive tests**

Run the command from Step 2.

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/app/interface/interfaceTokens.css apps/web/src/design-system
git commit -m "refactor(web): normalize interface geometry tokens"
```

### Task 2: Симметричная навигация и shell обоих режимов

**Files:**
- Modify: `apps/web/src/design-system/navigation/Tabs.tsx`
- Modify: `apps/web/src/design-system/navigation/Tabs.css`
- Modify: `apps/web/src/app/shell/AppShell.css`
- Modify: `apps/web/src/app/interface/ClassicShell.css`
- Modify: `apps/web/src/app/interface/TaskFirstShell.css`
- Modify: `apps/web/src/app/interface/interface-a.css`
- Test: `apps/web/src/design-system/navigation/Tabs.test.tsx`
- Test: `apps/web/src/app/shell/AppShell.test.tsx`
- Test: `apps/web/e2e/app-shell.spec.ts`
- Test: `apps/web/e2e/operational/interface-shell.spec.ts`

**Interfaces:**
- Consumes: geometry tokens from Task 1 and existing role-specific navigation data.
- Produces: equal-width phone tab rows, stable desktop tab rows, equal mobile-nav cells and safe content inset.

- [ ] **Step 1: Add failing structural and visual contracts**

Add a test-only item count marker to the tablist and assert it:

```tsx
expect(screen.getByRole('tablist', { name: 'Разделы парка' })).toHaveStyle({ '--rp-tab-count': '3' })
```

In Playwright, for each visible `.rp-shell__bottom-nav a, .rp-shell__bottom-nav button`, assert adjacent widths differ by at most 1 px and the content bottom padding is at least the nav height plus safe-area allowance.

- [ ] **Step 2: Run tests and observe failure**

Run: `npm test -- --run src/design-system/navigation/Tabs.test.tsx src/app/shell/AppShell.test.tsx`

Run: `npx playwright test e2e/app-shell.spec.ts e2e/operational/interface-shell.spec.ts --project=chromium`

Expected: at least the equal-cell or tab-count contract fails.

- [ ] **Step 3: Implement symmetric navigation**

Set the tab count from React:

```tsx
<div
  aria-label={ariaLabel}
  className="rp-tabs"
  role="tablist"
  style={{ '--rp-tab-count': items.length } as React.CSSProperties}
>
```

At phone widths use `grid-template-columns: repeat(var(--rp-tab-count), minmax(0, 1fr))`; keep overflow/scroll only for desktop collections that genuinely exceed available width. Make bottom navigation a grid with equal columns derived from its actual item count, keep active decoration inside the cell, and apply `padding-bottom: calc(var(--rp-shell-mobile-nav-height) + env(safe-area-inset-bottom) + var(--rp-space-4))` to both presentation shells.

- [ ] **Step 4: Run shell and navigation tests**

Run both commands from Step 2.

Expected: PASS in Classic and A fixtures.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/design-system/navigation apps/web/src/app/shell apps/web/src/app/interface apps/web/e2e/app-shell.spec.ts apps/web/e2e/operational/interface-shell.spec.ts
git commit -m "fix(web): align tabs and mobile navigation"
```

### Task 3: Экран работы и задачи в Classic и A

**Files:**
- Modify: `apps/web/src/domains/work/work.css`
- Modify: `apps/web/src/domains/work/TaskFirstTaskLayout.tsx`
- Modify: `apps/web/src/components/tracker/task-card.css`
- Modify: `apps/web/src/app/interface/interface-a.css`
- Test: `apps/web/src/domains/work/TaskFirstTaskLayout.test.tsx`
- Test: `apps/web/src/domains/work/IssueWorkbench.test.tsx`
- Test: `apps/web/e2e/operational/interface-work.spec.ts`
- Test: `apps/web/e2e/operational/work-tabs.spec.ts`
- Test: `apps/web/e2e/operational/interface-parity.spec.ts`

**Interfaces:**
- Consumes: tokens and tab geometry from Tasks 1–2.
- Produces: aligned task header, primary task tabs, separate open/closed row, repair form and robot context in both modes.

- [ ] **Step 1: Add failing work-screen geometry tests**

For widths 320, 390 and 1440 and both interface labels, assert:

```ts
const header = await page.locator('.rp-work-detail-pane [data-task-header]').boundingBox()
const body = await page.locator('.rp-work-detail-pane [data-task-body]').boundingBox()
expect(Math.abs(header!.x - body!.x)).toBeLessThanOrEqual(1)
expect(Math.abs((header!.x + header!.width) - (body!.x + body!.width))).toBeLessThanOrEqual(1)
```

Assert `Ремонт / Проверка / Чат` occupy the primary row, while `Открытые / Закрытые` occupy a separate row. Extend the existing 50-cycle parity test to keep the comment and file assertions.

- [ ] **Step 2: Run targeted tests and confirm failure**

Run: `npm test -- --run src/domains/work/TaskFirstTaskLayout.test.tsx src/domains/work/IssueWorkbench.test.tsx`

Run: `npx playwright test e2e/operational/interface-work.spec.ts e2e/operational/work-tabs.spec.ts e2e/operational/interface-parity.spec.ts --project=chromium`

Expected: FAIL on the new edge/row contract before CSS normalization.

- [ ] **Step 3: Normalize both task layouts**

Add stable `data-task-header` and `data-task-body` ownership markers. Replace local page/card paddings and radii with Task 1 tokens. Keep A's approved master/detail composition and Classic's existing information hierarchy. Remove nested borders where the parent already supplies a panel edge. At phone widths make primary actions full width and keep secondary actions wrapping at 12 px gaps.

- [ ] **Step 4: Run work tests**

Run both commands from Step 2.

Expected: PASS; the 50-mode cycle still reports zero writes and preserved draft/photo.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/domains/work apps/web/src/components/tracker/task-card.css apps/web/src/app/interface/interface-a.css apps/web/e2e/operational/interface-work.spec.ts apps/web/e2e/operational/work-tabs.spec.ts apps/web/e2e/operational/interface-parity.spec.ts
git commit -m "fix(web): normalize task layouts in both interfaces"
```

### Task 4: Роботы и проверка робота

**Files:**
- Modify: `apps/web/src/domains/robots/robots.css`
- Modify: `apps/web/src/domains/robots/robot-check.css`
- Modify: `apps/web/src/app/interface/classic-robot-check.css`
- Modify: `apps/web/src/domains/robots/RobotCheckNavigation.tsx`
- Modify: `apps/web/src/domains/robots/TaskFirstRobotLayout.tsx`
- Test: `apps/web/src/domains/robots/RobotCheckNavigation.test.tsx`
- Test: `apps/web/src/domains/robots/TaskFirstRobotLayout.test.tsx`
- Test: `apps/web/src/domains/robots/RobotCheckWorkspace.test.tsx`
- Test: `apps/web/e2e/operational/robots.spec.ts`
- Test: `apps/web/e2e/operational/responsive-visual.spec.ts`

**Interfaces:**
- Consumes: common tokens, tab rules and shell gutters.
- Produces: aligned robot list/detail and robot-check summary/diagnostics with usable markers.

- [ ] **Step 1: Add failing robot geometry coverage**

Extend responsive tests to select both `Классический` and `Новый А` at 320, 390, 768 and 1440. Assert summary/photo/diagnostic edges share the same outer guides, reading marker centers remain inside the illustration, visible markers remain at least 44×44 px, and `Состояние / Ошибки / Схема / Меню` do not overlap.

- [ ] **Step 2: Run tests and confirm the geometry contract fails**

Run: `npm test -- --run src/domains/robots/RobotCheckNavigation.test.tsx src/domains/robots/TaskFirstRobotLayout.test.tsx src/domains/robots/RobotCheckWorkspace.test.tsx`

Run: `npx playwright test e2e/operational/robots.spec.ts e2e/operational/responsive-visual.spec.ts --project=chromium --grep "robot|robot-check"`

Expected: FAIL on at least one mode/width edge alignment before normalization.

- [ ] **Step 3: Normalize robot layouts without changing diagnostic behavior**

Use shared card padding/radii for registry, identity, summary and diagnostic block. Preserve automatic robot view and existing reading placement logic. At phone widths give the illustration all available content width, use one tab row, and allow technical detail sections to collapse without changing their persisted state. Keep Classic and A selectors separate where their composition differs.

- [ ] **Step 4: Run robot tests**

Run both commands from Step 2.

Expected: PASS in both modes; camera, markers and diagnostics behavior unchanged.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/domains/robots apps/web/src/app/interface/classic-robot-check.css apps/web/e2e/operational/robots.spec.ts apps/web/e2e/operational/responsive-visual.spec.ts
git commit -m "fix(web): align robot and diagnostic interfaces"
```

### Task 5: Остальные домены и администрирование

**Files:**
- Modify: `apps/web/src/domains/inventory/inventory.css`
- Modify: `apps/web/src/domains/management/management.css`
- Modify: `apps/web/src/domains/campaigns/campaigns.css`
- Modify: `apps/web/src/domains/analytics/analytics.css`
- Modify: `apps/web/src/domains/insights/insights.css`
- Modify: `apps/web/src/domains/shift/overview.css`
- Modify: `apps/web/src/components/admin/AdminOpsPanel.css`
- Modify: `apps/web/src/components/admin/OpsAlert.css`
- Test: `apps/web/src/domains/inventory/InventoryPage.test.tsx`
- Test: `apps/web/src/domains/management/ManagementPage.test.tsx`
- Test: `apps/web/src/domains/campaigns/CampaignsPage.test.tsx`
- Test: `apps/web/src/domains/analytics/AnalyticsWorkspace.test.tsx`
- Test: `apps/web/e2e/operational/interface-inventory.spec.ts`
- Test: `apps/web/e2e/operational/interface-overview.spec.ts`
- Test: `apps/web/e2e/operational/interface-reports.spec.ts`
- Test: `apps/web/e2e/operational/interface-campaigns.spec.ts`
- Test: `apps/web/e2e/operational/analytics.spec.ts`
- Test: `apps/web/e2e/operational/admin-settings.spec.ts`

**Interfaces:**
- Consumes: common geometry and shell rules from Tasks 1–2.
- Produces: consistent cards/forms/selectors across Inventory, Management, Reports, Campaigns, Analytics and Overview.

- [ ] **Step 1: Add failing cross-domain contracts**

For each route fixture at 320, 390 and 1440 and both modes, assert no horizontal overflow, first/last content edges use the shell gutter, checkbox boxes are at most 24 px while their label rows are at least 44 px, phone section selection is one full-width control, and nested bordered panels do not share the same bounding box.

Use this overflow assertion:

```ts
expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true)
```

- [ ] **Step 2: Run domain tests and observe failures**

Run: `npm test -- --run src/domains/inventory/InventoryPage.test.tsx src/domains/management/ManagementPage.test.tsx src/domains/campaigns/CampaignsPage.test.tsx src/domains/analytics/AnalyticsWorkspace.test.tsx`

Run: `npx playwright test e2e/operational/interface-inventory.spec.ts e2e/operational/interface-overview.spec.ts e2e/operational/interface-reports.spec.ts e2e/operational/interface-campaigns.spec.ts e2e/operational/analytics.spec.ts e2e/operational/admin-settings.spec.ts --project=chromium`

Expected: FAIL where local paddings/radii conflict with the common contract.

- [ ] **Step 3: Migrate domain CSS to common geometry**

Replace page-level literal `padding`, `gap` and `border-radius` values with semantic tokens. Preserve chart- and robot-specific geometry. Remove redundant inner frames, normalize action rows, keep phone checkboxes compact with a 44 px clickable label, and ensure management/inventory selectors span the content width without adding a second page gutter.

- [ ] **Step 4: Run domain tests**

Run both commands from Step 2.

Expected: PASS for all routes and both interface modes.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/domains apps/web/src/components/admin apps/web/e2e/operational/interface-inventory.spec.ts apps/web/e2e/operational/interface-overview.spec.ts apps/web/e2e/operational/interface-reports.spec.ts apps/web/e2e/operational/interface-campaigns.spec.ts apps/web/e2e/operational/analytics.spec.ts apps/web/e2e/operational/admin-settings.spec.ts
git commit -m "fix(web): normalize remaining interface screens"
```

### Task 6: Полная визуальная приёмка Classic и A

**Files:**
- Modify: `apps/web/e2e/operational/interface-visual-acceptance.spec.ts`
- Modify: `apps/web/e2e/operational/responsive-visual.spec.ts`
- Modify: `apps/web/e2e/operational/visual-contract.spec.ts`
- Modify: `apps/web/e2e/operational/routeFixtures.ts`
- Update: intentional snapshots under `apps/web/e2e/operational/*-snapshots/`

**Interfaces:**
- Consumes: completed geometry of Tasks 1–5.
- Produces: repeatable acceptance evidence for all key screens, modes, themes and widths.

- [ ] **Step 1: Expand the acceptance matrix before updating snapshots**

Run every key screen in both modes:

```ts
for (const mode of ['Классический', 'Новый А'] as const)
  for (const theme of ['light', 'dark'] as const)
    for (const width of [320, 390, 412, 768, 1024, 1440] as const) {
      // open fixture, select mode, assert responsive/a11y/geometry contracts
    }
```

Cover Overview, Work list/detail, Robots, Robot check, Inventory, Reports, Campaigns, Analytics, Users, Roles, Settings and Robot-check settings. Add boundary cases 599/600, 899/900 and 1199/1200 and retain the 200% zoom case.

- [ ] **Step 2: Run acceptance without snapshot updates**

Run: `npx playwright test e2e/operational/interface-visual-acceptance.spec.ts e2e/operational/responsive-visual.spec.ts e2e/operational/visual-contract.spec.ts --project=chromium`

Expected: only intentional snapshot mismatches are allowed; geometry, overflow and a11y assertions must already pass.

- [ ] **Step 3: Inspect and update only intentional snapshots**

Open failed images, reject any clipping, asymmetric edges, double frames or mismatched radii, fix source CSS, rerun Step 2, then run:

`npx playwright test e2e/operational/interface-visual-acceptance.spec.ts e2e/operational/responsive-visual.spec.ts --project=chromium --update-snapshots`

- [ ] **Step 4: Run final verification**

Run:

```bash
npm test -- --run
npm run lint
npm run check:contrast
npm run build
npx playwright test e2e/app-shell.spec.ts e2e/operational/interface-shell.spec.ts e2e/operational/interface-work.spec.ts e2e/operational/work-tabs.spec.ts e2e/operational/interface-parity.spec.ts e2e/operational/robots.spec.ts e2e/operational/interface-inventory.spec.ts e2e/operational/interface-overview.spec.ts e2e/operational/interface-reports.spec.ts e2e/operational/interface-campaigns.spec.ts e2e/operational/analytics.spec.ts e2e/operational/admin-settings.spec.ts e2e/operational/interface-visual-acceptance.spec.ts e2e/operational/responsive-visual.spec.ts e2e/operational/visual-contract.spec.ts --project=chromium
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit final evidence**

```bash
git add apps/web/e2e/operational
git commit -m "test(web): cover geometry in both interface modes"
```
