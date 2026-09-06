import { expect, test, type Page, type TestInfo } from '@playwright/test'
import type { AdminRole, AdminUser, Report } from '../../src/api'
import { assertNoSeriousA11yViolations } from '../support/assertA11y'
import { installOperational, parkNorth, parkSouth, settlePage } from './fixtures'

const catalog = [
  { key: 'nav.tasks', label: 'Работа', category: 'nav', sort_order: 1 },
  { key: 'nav.reports', label: 'Репорты', category: 'nav', sort_order: 2 },
  { key: 'reports.create', label: 'Создавать репорты', category: 'action', sort_order: 3 },
]
const roles: AdminRole[] = [
  { id: 1, slug: 'mechanic', name: 'Механик', description: 'Осмотр и ремонт роботов', is_system: true, is_active: true, permissions: ['nav.tasks', 'nav.reports', 'reports.create'], user_count: 8 },
  { id: 2, slug: 'shift_lead', name: 'Старший смены', description: 'Координация команды', is_system: false, is_active: true, permissions: ['nav.tasks'], user_count: 2 },
]
const account: AdminUser = {
  id: 2, username: 'mechanic.shift', role: 'mechanic', role_id: 1, access_status: 'approved', is_active: true,
  tracker_login: 'mechanic.shift', must_change_password: false, parks: [parkNorth],
  permissions: roles[0].permissions, role_permissions: roles[0].permissions,
}
const report: Report = {
  id: 9, kind: 'mechanic_problem', status: 'returned', park_id: 7, author_user_id: 100,
  target_role: 'operator', tracker_key: 'ROBOPARK-42', tracker_url: 'https://tracker.example.invalid/ROBOPARK-42',
  title: 'Повторная проверка крепления переднего колеса робота 447',
  body: 'После осмотра обнаружен люфт крепления. Требуется уточнение времени последней замены детали.',
  return_comment: 'Укажите время проверки и приложите фото крепления.', parent_report_id: null,
  created_at: '2026-09-02T08:00:00Z', updated_at: '2026-09-02T09:00:00Z', resolved_at: null,
}

async function evidence(page: Page, info: TestInfo, name: string) {
  await settlePage(page)
  const problems = await page.getByRole('main').evaluate(main => {
    const visible = (element: Element) => element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden'
    const controlProblems = Array.from(main.querySelectorAll('button,a,input,select,textarea')).filter(visible).flatMap(element => {
      const target = element.matches('input[type="checkbox"]') ? (element as HTMLInputElement).labels?.[0] : element
      if (!target) return ['missing label']
      const box = target.getBoundingClientRect()
      return box.width < 43.9 || box.height < 43.9 || box.left < 0 || box.right > innerWidth + 1
        ? [`${element.tagName} ${element.textContent?.trim().slice(0, 30)}: ${box.width}×${box.height} at ${box.left}`] : []
    })
    const checkboxProblems = Array.from(main.querySelectorAll('.admin-perm-check input')).filter(visible).flatMap(element => {
      const box = element.getBoundingClientRect()
      return box.width > 24 || box.height > 24 ? [`checkbox oversized: ${box.width}×${box.height}`] : []
    })
    const passwordProblems = Array.from(main.querySelectorAll('.password-field')).filter(visible).flatMap(element => {
      const input = element.querySelector('input')!
      const toggle = element.querySelector('button')!
      return parseFloat(getComputedStyle(input).paddingRight) < toggle.getBoundingClientRect().width + 8
        ? ['password text overlaps visibility control'] : []
    })
    const geometryProblems = Array.from(main.querySelectorAll('.field input:not([type="checkbox"]):not([type="file"]), .field select, .field textarea')).filter(visible).flatMap(element => {
      const style = getComputedStyle(element)
      return style.borderTopLeftRadius !== style.getPropertyValue('--rp-radius-control').trim()
        ? [`field uses legacy radius: ${style.borderTopLeftRadius}`] : []
    })
    return [...controlProblems, ...checkboxProblems, ...passwordProblems, ...geometryProblems]
  })
  expect(problems).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await assertNoSeriousA11yViolations(page)
  await page.evaluate(() => { (document.activeElement as HTMLElement | null)?.blur(); window.scrollTo(0, 0) })
  await page.mouse.move(0, 0)
  await page.screenshot({ path: info.outputPath(`${name}.png`), fullPage: true, animations: 'disabled' })
}

test('a late completed report refresh cannot close a different selected report', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const first = { ...report, status: 'open', title: 'Репорт A' }
  const second = { ...first, id: 10, title: 'Репорт B' }
  let release!: () => void
  const pending = new Promise<void>(resolve => { release = resolve })
  let completed = false
  let listPending = false
  await installOperational(page, { role: 'operator', routes: [
    { method: 'GET', path: '/api/reports/inbox', handler: async () => {
      if (completed) { listPending = true; await pending; return { json: [second] } }
      return { json: [first, second] }
    } },
    { method: 'GET', path: '/api/reports/mine', handler: () => ({ json: [] }) },
    { method: 'GET', path: '/api/reports/9', handler: () => ({ json: completed ? { ...first, status: 'done' } : first }) },
    { method: 'GET', path: '/api/reports/10', handler: () => ({ json: second }) },
    { method: 'POST', path: '/api/reports/9/done', handler: () => { completed = true; return { json: { ...first, status: 'done' } } } },
  ] })
  await page.goto('/reports/9?park=7&pane=inbox')
  await page.getByRole('button', { name: 'Готово', exact: true }).click()
  await expect.poll(() => listPending).toBe(true)
  await page.getByRole('button', { name: 'Открыть репорт Репорт B', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Репорт B', exact: true })).toBeVisible()
  const response = page.waitForResponse(value => value.url().includes('/api/reports/inbox'))
  release()
  await response
  await settlePage(page)
  await expect(page).toHaveURL('/reports/10?park=7&pane=inbox')
  await expect(page.getByRole('heading', { name: 'Репорт B', exact: true })).toBeVisible()
})

test('late account PATCH updates its row without replacing a different selected account', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const other = { ...account, id: 3, username: 'mechanic.other', tracker_login: 'other.login' }
  let release!: () => void
  const pending = new Promise<void>(resolve => { release = resolve })
  let saving = false
  let currentAccount = account
  await installOperational(page, { role: 'royal', routes: [
    { method: 'GET', path: '/api/admin/users', handler: () => ({ json: [currentAccount, other] }) },
    { method: 'GET', path: '/api/admin/roles', handler: () => ({ json: roles }) },
    { method: 'GET', path: '/api/admin/roles/permissions/catalog', handler: () => ({ json: catalog }) },
    { method: 'PATCH', path: '/api/admin/users/2', handler: async () => { saving = true; await pending; currentAccount = { ...account, tracker_login: 'saved.first' }; return { json: currentAccount } } },
  ] })
  await page.goto('/admin/users?park=7')
  await page.getByRole('button', { name: 'Открыть аккаунт mechanic.shift', exact: true }).click()
  const detail = page.getByRole('region', { name: 'Детали' })
  await detail.getByRole('button', { name: 'Сохранить', exact: true }).click()
  await expect.poll(() => saving).toBe(true)
  await page.getByRole('button', { name: 'Открыть аккаунт mechanic.other', exact: true }).click()
  const response = page.waitForResponse(value => value.request().method() === 'PATCH')
  release()
  await response
  await settlePage(page)
  await expect(detail.getByLabel('Tracker login', { exact: true })).toHaveValue('other.login')
  await expect(page.getByText('Изменения сохранены', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: 'Открыть аккаунт mechanic.shift', exact: true }).click()
  await expect(detail.getByLabel('Tracker login', { exact: true })).toHaveValue('saved.first')
})

test('owner role displays immutable effective permissions despite empty stored defaults', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 900 })
  const ownerRole = { ...roles[0], id: 3, slug: 'royal', name: 'Владелец', description: 'Полный доступ к управлению системой', permissions: [] }
  await installOperational(page, { role: 'royal', routes: [
    { method: 'GET', path: '/api/admin/roles', handler: () => ({ json: [ownerRole] }) },
    { method: 'GET', path: '/api/admin/roles/permissions/catalog', handler: () => ({ json: [...catalog, { key: 'users.approve', label: 'Одобрять регистрации', category: 'action', sort_order: 4 }] }) },
  ] })
  await page.goto('/admin/roles?park=7&tab=parks')
  await page.getByRole('button', { name: 'Открыть роль Владелец', exact: true }).click()
  const detail = page.getByRole('region', { name: 'Детали' })
  await expect(detail.getByRole('region', { name: 'Итоговые доступы' })).toContainText('Одобрять регистрации')
  for (const checkbox of await detail.getByRole('checkbox').all()) {
    await expect(checkbox).toBeChecked()
    await expect(checkbox).toBeDisabled()
  }
  await expect(page.getByRole('navigation', { name: 'Разделы управления' }).getByRole('link', { name: 'Роли и доступы' })).toHaveAttribute('aria-current', 'page')
  await evidence(page, info, 'roles-owner-light-390')
})

for (const width of [390, 1440]) for (const theme of ['light', 'dark'] as const) {
  test(`management hub ${width}px ${theme}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
    await installOperational(page, { role: 'royal' })
    await page.goto('/admin?park=7')
    await expect(page.getByRole('button', { name: 'Сменить парк' })).toContainText('Северный парк')
    await expect(page.getByRole('heading', { name: 'Управление', level: 1, exact: true })).toBeVisible()
    await evidence(page, info, `management-${theme}-${width}`)
  })
}

for (const width of [320, 390, 1440]) for (const theme of ['light', 'dark'] as const) {
  test(`accounts owner workflow ${width}px ${theme}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
    let payload: unknown
    let removed = false
    await installOperational(page, { role: 'royal', routes: [
      { method: 'GET', path: '/api/admin/users', handler: () => ({ json: [account] }) },
      { method: 'GET', path: '/api/admin/roles', handler: () => ({ json: roles }) },
      { method: 'GET', path: '/api/admin/roles/permissions/catalog', handler: () => ({ json: catalog }) },
      { method: 'PATCH', path: '/api/admin/users/2', handler: async request => { payload = await request.json(); return { json: { ...account, parks: [parkSouth], must_change_password: true } } } },
      { method: 'DELETE', path: '/api/admin/users/2', handler: () => { removed = true; return { status: 204 } } },
    ] })
    await page.goto('/admin/users?park=7')
    await page.getByRole('button', { name: /mechanic.shift/ }).click()
    const detail = page.getByRole('region', { name: 'Детали' })
    await expect(detail).toBeVisible()
    await expect(detail.getByRole('region', { name: 'Итоговые доступы' })).toContainText('Создавать репорты')
    await evidence(page, info, `accounts-${theme}-${width}`)
    await detail.getByLabel('Новый пароль', { exact: true }).fill('NewPassword!2026')
    await detail.getByLabel('Требовать смену пароля при входе').check()
    await detail.getByLabel('Северный парк', { exact: true }).uncheck()
    await detail.getByLabel('Южный парк', { exact: true }).check()
    await detail.getByRole('button', { name: 'Сохранить', exact: true }).click()
    await expect.poll(() => payload).toMatchObject({ password: 'NewPassword!2026', park_ids: [8], must_change_password: true })
    await expect(detail.getByLabel('Новый пароль', { exact: true })).toHaveValue('')
    page.once('dialog', dialog => dialog.accept())
    await detail.getByRole('region', { name: 'Опасные действия' }).getByRole('button', { name: 'Удалить аккаунт' }).click()
    await expect.poll(() => removed).toBe(true)
    await expect(page.getByText('Аккаунт удалён', { exact: true })).toBeVisible()
    if (width < 900) {
      await page.getByRole('button', { name: 'Назад к списку' }).click()
      await expect(page.getByRole('region', { name: 'Список' })).toBeVisible()
    }
  })

  test(`roles editor workflow ${width}px ${theme}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
    let payload: unknown
    await installOperational(page, { role: 'royal', routes: [
      { method: 'GET', path: '/api/admin/roles', handler: () => ({ json: roles }) },
      { method: 'GET', path: '/api/admin/roles/permissions/catalog', handler: () => ({ json: catalog }) },
      { method: 'PATCH', path: '/api/admin/roles/2', handler: async request => { payload = await request.json(); return { json: { ...roles[1], permissions: ['reports.create'] } } } },
    ] })
    await page.goto('/admin/roles?park=7')
    await page.getByRole('button', { name: /Старший смены/ }).click()
    const detail = page.getByRole('region', { name: 'Детали' })
    await detail.getByLabel('Работа', { exact: true }).uncheck()
    await detail.getByLabel('Создавать репорты', { exact: true }).check()
    await expect(detail.getByRole('region', { name: 'Итоговые доступы' })).not.toContainText('Работа')
    await detail.getByRole('button', { name: 'Сохранить', exact: true }).click()
    await expect.poll(() => payload).toMatchObject({ permissions: ['reports.create'] })
    await evidence(page, info, `roles-${theme}-${width}`)
    await expect(page.getByRole('navigation', { name: 'Разделы управления' }).getByRole('link', { name: 'Роли и доступы' })).toHaveAttribute('aria-current', 'page')
    if (width < 900) await page.getByRole('button', { name: 'Назад к списку' }).click()
    await page.getByRole('button', { name: 'Новая роль', exact: true }).click()
    await expect(detail.getByLabel('Slug (латиница)')).toHaveValue('')
  })

  test(`reports list detail and create ${width}px ${theme}`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(value => localStorage.setItem('robopark-theme', value), theme)
    await installOperational(page, { role: 'driver', routes: [
      { method: 'GET', path: '/api/reports/mine', handler: () => ({ json: [report] }) },
      { method: 'GET', path: '/api/reports/9', handler: () => ({ json: report }) },
    ] })
    await page.goto('/reports?park=7&status=returned')
    await page.getByRole('button', { name: /Повторная проверка/ }).click()
    const list = page.getByRole('region', { name: 'Список' })
    const detail = page.getByRole('region', { name: 'Детали' })
    await expect(detail.getByRole('heading', { name: report.title })).toBeVisible()
    if (width < 900) await expect(list).toBeHidden()
    else await expect(list).toBeVisible()
    await evidence(page, info, `reports-${theme}-${width}`)
    if (width < 900) await page.getByRole('button', { name: 'Назад к списку' }).click()
    else await detail.getByRole('button', { name: 'Закрыть', exact: true }).click()
    await expect(page).toHaveURL('/reports?park=7&status=returned')
    await expect(page.getByLabel('Статус репортов')).toHaveValue('returned')
    await page.getByRole('link', { name: 'Создать репорт', exact: true }).click()
    await expect(page.getByRole('textbox', { name: 'Заголовок *' })).toBeVisible()
  })
}
