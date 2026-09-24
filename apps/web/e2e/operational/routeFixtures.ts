import { expect, type Page } from '@playwright/test'
import type { AdminRole, AdminUser, Campaign, CampaignDetail, ParkRequest, PermissionCatalogItem, Report, ScheduleEntry, ScheduleParticipant, User } from '../../src/api'
import { canAccessRoute } from '../../src/app/routing/accessPolicy'
import { ROUTE_MANIFEST, type AppRouteId, type RouteManifestItem } from '../../src/app/routing/routeManifest'
import { analyticsFixture } from '../../src/domains/analytics/analytics.test-support'
import type { MockRoute } from '../support/mockApi'
import { installOperational, issue, parkNorth, parkSouth } from './fixtures'

const routeReport: Report = {
  id: 1, kind: 'mechanic_problem', status: 'open', park_id: 7, author_user_id: 101,
  target_role: 'operator', tracker_key: 'ROBOPARK-42', tracker_url: null,
  title: 'Проверить колесо', body: 'Робот требует осмотра.', parent_report_id: null,
  return_comment: null, created_at: '2026-09-02T09:00:00Z', updated_at: '2026-09-02T09:00:00Z', resolved_at: null,
  attachments: [{ id: 11, kind: 'device_photo', filename: 'inspection.jpg', content_type: 'image/jpeg', size_bytes: 128 }],
}

const routeCampaign: Campaign = {
  id: 4, kind: 'service_company', name: 'Осенняя сервисная кампания', tracker_tag: 'service-2026',
  starts_on: '2026-09-01', due_on: '2026-10-01', is_active: true, park_ids: [7], park_names: ['Северный парк'],
  total_count: 2, completed_count: 1, pending_review_count: 0, remaining_count: 1, percent_complete: 50, overdue: false,
}
const routeCampaignDetail: CampaignDetail = {
  ...routeCampaign,
  open_tickets: [{ key: 'ROBOPARK-42', summary: 'Проверить колесо после сервиса', status: 'Открыт', park_id: 7, park_name: 'Северный парк', robot: '447', url: 'https://tracker.example.invalid/ROBOPARK-42', completed_at: null, completed_by: null, comment: null, report_id: null, review_status: null, tracker_transition: null }],
  closed_tickets: [],
}
const routeRequests: ParkRequest[] = [{ id: 5, user_id: 101, park_id: 8, status: 'pending', created_at: '2026-09-02T09:00:00Z', resolved_at: null, resolved_by: null, username: 'operator-e2e' }]
const routeRoles: AdminRole[] = [{ id: 1, slug: 'mechanic', name: 'Механик', description: 'Работа с задачами', is_system: true, is_active: true, permissions: ['nav.inventory'], user_count: 1 }]
const routeCatalog: PermissionCatalogItem[] = [{ key: 'nav.inventory', category: 'nav', label: 'Склад', sort_order: 75 }]
const routeUsers: AdminUser[] = [{ id: 101, username: 'route-admin', role: 'admin', role_id: 1, access_status: 'approved', is_active: true, tracker_login: 'admin.test', must_change_password: false, parks: [parkNorth], permissions: ['nav.admin'], role_permissions: ['nav.admin'] }]
export const routeSchedule: ScheduleEntry = {
  id: 'route-shift', owner_user_id: 100, park_id: parkNorth.id, kind: 'shift',
  start_at: '2026-09-02T09:00:00+03:00', end_at: '2026-09-02T21:00:00+03:00',
  source: 'route-fixture', series_id: null, created_by_user_id: 104, updated_by_user_id: 104,
  created_at: '2026-09-01T10:00:00Z', updated_at: '2026-09-01T10:00:00Z', warnings: [],
}
const routeScheduleParticipants: ScheduleParticipant[] = [
  { id: 100, display_name: 'Анна Механик', role: 'mechanic' },
  { id: 101, display_name: 'Олег Оператор', role: 'operator' },
]

export function geometryRouteIdsFor(user: User): AppRouteId[] {
  return ROUTE_MANIFEST
    .filter(route => route.surface === 'shell' && route.redirectTo == null && canAccessRoute(user, route.id))
    .map(route => route.id)
}
function routeMockRoutes(): MockRoute[] {
  return [
    { method: 'GET', path: '/api/analytics', handler: request => {
      const params = new URL(request.url).searchParams
      return { json: analyticsFixture(Number(params.get('park_id')), Number(params.get('days')), params.get('bucket') === '2h' ? '2h' : '1d') }
    } },
    { method: 'GET', path: '/api/operator/parks', handler: () => ({ json: [parkNorth] }) },
    { method: 'GET', path: '/api/operator/available-parks', handler: () => ({ json: [parkSouth] }) },
    { method: 'GET', path: '/api/operator/park-requests', handler: () => ({ json: routeRequests }) },
    { method: 'GET', path: '/api/schedules', handler: () => ({ json: [routeSchedule] }) },
    { method: 'GET', path: '/api/schedules/participants', handler: () => ({ json: routeScheduleParticipants }) },
    { method: 'GET', path: '/api/campaigns', handler: () => ({ json: [routeCampaign] }) },
    { method: 'GET', path: '/api/campaigns/4', handler: () => ({ json: routeCampaignDetail }) },
    { method: 'GET', path: '/api/reports/mine', handler: () => ({ json: [routeReport] }) },
    { method: 'GET', path: '/api/reports/inbox', handler: () => ({ json: [routeReport] }) },
    { method: 'GET', path: '/api/reports/1', handler: () => ({ json: routeReport }) },
    { method: 'GET', path: '/api/reports/badge', handler: () => ({ json: { count: 1 } }) },
    { method: 'GET', path: '/api/admin/users', handler: () => ({ json: routeUsers }) },
    { method: 'GET', path: '/api/admin/roles', handler: () => ({ json: routeRoles }) },
    { method: 'GET', path: '/api/admin/roles/permissions/catalog', handler: () => ({ json: routeCatalog }) },
    { method: 'GET', path: '/api/admin/emergency/sections', handler: () => ({ json: [{
      id: 'wheels',
      title: 'Колёса',
      sort_order: 0,
      is_enabled: true,
      formatter: null,
      meta: null,
      roles: ['mechanic', 'operator', 'admin', 'royal', 'driver'],
      fields: [],
    }] }) },
    { method: 'GET', path: '/api/admin/diagnostic-rules', handler: () => ({ json: [] }) },
    { method: 'GET', path: '/api/admin/emergency-readings', handler: () => ({ json: [] }) },
    { method: 'GET', path: '/api/admin/park-requests', handler: () => ({ json: routeRequests }) },
    { method: 'GET', path: '/api/admin/settings/integrations', handler: () => ({ json: { tracker_token_masked: 'set', tracker_token_updated_at: '2026-09-02T09:00:00Z', emergency_cookie_masked: 'set', emergency_cookie_updated_at: '2026-09-02T09:00:00Z', emergency_cookie_valid: true, emergency_cookie_status: 'valid', emergency_cookie_checked_at: '2026-09-02T09:00:00Z', emergency_cookie_checked_robot: '447' } }) },
    { method: 'GET', path: '/api/admin/settings/tracker-policy', handler: () => ({ json: { operator_show_untagged: true, operator_show_raw: false, operator_show_firmware_profile: false, mechanic_can_write: true } }) },
    { method: 'GET', path: '/api/admin/settings/screenshot-guard', handler: () => ({ json: { operator: false, mechanic: false, admin: false, royal: false, driver: false } }) },
    { method: 'GET', path: '/api/admin/settings/registration-password', handler: () => ({ json: { configured: true, password_masked: 'set', updated_at: '2026-09-02T09:00:00Z' } }) },
    { method: 'GET', path: '/api/admin/ops/job', handler: () => ({ json: { id: '', state: 'idle' } }) },
    { method: 'GET', path: '/api/admin/ops/system-health', handler: () => ({ json: { generated_at: '2026-09-02T09:00:00Z', services: [] } }) },
    { method: 'GET', path: '/api/admin/ops/available-update', handler: () => ({ json: { state: 'disabled', checked_at: null, release: null } }) },
  ]
}

export function fixturePath(route: RouteManifestItem): string {
  if (route.id === 'work-issue') return '/work/ROBOPARK-42?park=7'
  if (route.id === 'robot-detail') return '/robots/YASADR00000000447?park=7'
  if (route.id === 'robot-check') return '/robots/YASADR00000000447/check?park=7&tab=state'
  if (route.id === 'campaign-detail') return '/campaigns/4?park=7'
  if (route.id === 'report-detail') return '/reports/1?park=7'
  return `${route.path}${route.surface === 'shell' ? '?park=7' : ''}`
}

export async function openRouteFixture(page: Page, routeId: AppRouteId, user: User, options: { routes?: MockRoute[] } = {}): Promise<void> {
  const route = ROUTE_MANIFEST.find(item => item.id === routeId)
  if (!route) throw new Error(`Unknown route fixture: ${routeId}`)
  const loadedIssue = routeId === 'work-issue' ? {
    ...issue,
    claim: { park_id: parkNorth.id },
    workflow: {
      owner: { display: 'Механик смены', login: 'mechanic-e2e' },
      review_state: null,
      display_status: 'in_progress' as const,
      sync_state: 'synced' as const,
      queued_at: '2026-09-02T08:00:00Z',
      queued_at_source: 'tracker_history' as const,
      has_current_cycle_comment: true,
    },
  } : undefined
  await installOperational(page, { user, issue: loadedIssue, routes: [...(options.routes ?? []), ...routeMockRoutes()] })
  await page.goto(fixturePath(route))
  await expect(page.locator('main')).toBeVisible()
  await expect(routeReadyMarker(page, routeId)).toBeVisible()
  if (routeId === 'operator-parks') {
    await expect(page.getByText('Парк #8', { exact: true })).toBeVisible()
    const availableParkPanel = page.locator('section.panel').filter({
      has: page.getByRole('heading', { name: 'Запросить парк', exact: true, level: 2 }),
    })
    await availableParkPanel.getByRole('button', { name: 'Запросить парк', exact: true }).click()
    const requestDialog = page.getByRole('dialog', { name: 'Запросить парк' })
    await expect(requestDialog.getByRole('option', { name: 'Южный парк (south)', exact: true })).toHaveText('Южный парк (south)')
    await expect(requestDialog.getByLabel('Парк', { exact: true })).toHaveValue('8')
    await requestDialog.getByRole('button', { name: 'Закрыть', exact: true }).filter({ hasText: 'Закрыть' }).click()
    await expect(requestDialog).toBeHidden()
  }
  if (routeId === 'admin-users') {
    await page.getByRole('button', { name: 'Открыть аккаунт route-admin', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'route-admin', exact: true })).toBeVisible()
  }
  if (routeId === 'admin-roles') {
    await page.getByRole('button', { name: 'Открыть роль Механик', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Редактор: Механик', exact: true })).toBeVisible()
  }
}

export async function assertRouteSemanticContracts(page: Page, routeId: AppRouteId): Promise<void> {
  if (routeId === 'work-issue') {
    const detail = page.locator('.rp-work-detail-pane')
    await expect(detail.getByText('ROBOPARK-42', { exact: true })).toHaveCount(1)
    await expect(detail.getByRole('heading', { name: 'Проверить переднее левое колесо робота 447', exact: true })).toHaveCount(1)
  }

  if (routeId === 'campaigns') {
    const escapedMetrics = await page.locator('.campaign-card').evaluateAll(cards => cards.flatMap(card => {
      const outer = card.getBoundingClientRect()
      return Array.from(card.querySelectorAll<HTMLElement>('.campaign-card__heading, .campaign-metrics'))
        .filter(element => {
          const box = element.getBoundingClientRect()
          return box.left < outer.left - 1 || box.right > outer.right + 1
        })
        .map(element => element.className)
    }))
    expect(escapedMetrics, 'campaign content escapes its card').toEqual([])
  }

  const masterDetail = page.locator('.rp-master-detail[data-detail-open="true"] .rp-master-detail__detail')
  if (await masterDetail.count()) {
    const meaningful = await masterDetail.evaluate(element => (element.textContent ?? '').trim().length)
    expect(meaningful, `empty detail pane on ${routeId}`).toBeGreaterThan(0)
  }

  const nav = page.locator('.rp-shell__bottom-nav')
  if (await nav.isVisible().catch(() => false)) {
    const duplicateCurrent = await nav.locator('[aria-current="page"]').count()
    expect(duplicateCurrent, `multiple current mobile destinations on ${routeId}`).toBeLessThanOrEqual(1)
  }
}

function routeReadyMarker(page: Page, routeId: AppRouteId) {
  switch (routeId) {
    case 'overview': return page.getByRole('heading', { name: 'Очередь внимания' })
    case 'operator-parks': return page.locator('.park-card-title', { hasText: 'Северный парк' })
    case 'work': return page.locator('.rp-work-entities').first()
    case 'work-issue': return page.getByRole('heading', { name: 'Задача ROBOPARK-42', exact: true })
    case 'robots': return page.locator('.rp-robots-search-panel')
    case 'robot-detail': return page.getByRole('heading', { name: /^\u0420\u043e\u0431\u043e\u0442 (?:447|YASADR00000000447)$/ })
    case 'robot-check': return page.getByRole('tabpanel', { name: 'Состояние' })
    case 'legacy-robot-check': return page.locator('.rp-robots-search-panel')
    case 'inventory': return page.getByText('ABC-1', { exact: true })
    case 'campaigns': return page.getByRole('heading', { name: routeCampaign.name, exact: true })
    case 'campaign-detail': return page.getByRole('heading', { name: routeCampaignDetail.name, exact: true })
    case 'reports': return page.getByRole('button', { name: `Открыть репорт ${routeReport.title}`, exact: true })
    case 'reports-new': return page.getByRole('textbox', { name: 'Заголовок *', exact: true })
    case 'report-detail': return page.getByText(routeReport.body, { exact: true })
    case 'schedule': return page.locator('.rp-page-layout.rp-schedule')
    case 'analytics': return page.locator('.rp-analytics-park .rp-analytics-value').filter({ hasText: '2 задач' }).first()
    case 'admin': return page.getByRole('heading', { name: 'Управление', exact: true, level: 1 })
    case 'admin-settings': return page.getByText('Tracker OAuth', { exact: true })
    case 'admin-users': return page.getByRole('button', { name: 'Открыть аккаунт route-admin', exact: true })
    case 'admin-roles': return page.getByText('Механик', { exact: true })
    case 'admin-tracker': return page.locator('.rp-work-entities').first()
    case 'admin-robot-check': return page.getByRole('button', { name: 'Открыть раздел Колёса', exact: true })
    default: return page.locator('main')
  }
}

export async function assertResponsiveContracts(page: Page, _width: number): Promise<void> {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true)
  expect(await page.locator('[data-interface="task-first"]').count()).toBe(0)
  const overflow = await page.evaluate(() => ({
    amount: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    offenders: Array.from(document.querySelectorAll('body *'))
      .filter((element) => element.getBoundingClientRect().right + window.scrollX > document.documentElement.clientWidth + 1)
      .map((element) => `${element.tagName.toLowerCase()}.${element.className}: ${Math.round(element.getBoundingClientRect().right + window.scrollX)}`)
      .slice(0, 12),
    layout: ['.rp-app-shell', '.rp-shell__sidebar', '.rp-shell__main-column', '.rp-shell__content', '.rp-page-layout', '.rp-workbench', '.rp-work-list-pane', '.rp-work-detail-pane']
      .map((selector) => {
        const element = document.querySelector(selector)
        if (!element) return `${selector}: missing`
        const box = element.getBoundingClientRect()
        const style = getComputedStyle(element)
        return `${selector}: x=${Math.round(box.x + window.scrollX)} w=${Math.round(box.width)} scroll=${element.scrollWidth}/${element.clientWidth} cols=${style.gridTemplateColumns}`
      }),
  }))
  expect(overflow.amount, `horizontal overflow: ${overflow.offenders.join(', ')}; layout: ${overflow.layout.join('; ')}`).toBeLessThanOrEqual(0)
  const violations = await page.evaluate(() => {
    const failures: string[] = []
    const visible = (element: Element) => {
      const style = getComputedStyle(element)
      const box = element.getBoundingClientRect()
      return box.width > 0 && box.height > 0 && style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0'
    }
    const name = (element: Element) => `${element.tagName.toLowerCase()}#${element.id}.${element.className} ${(element.getAttribute('aria-label') || element.textContent || '').trim().slice(0, 70)}`
    for (const element of document.querySelectorAll('body *')) {
      if (!visible(element) || element.closest('[aria-hidden="true"],svg,script,style,option')) continue
      const control = element.matches('button,a,input,select,textarea')
      const hasText = Array.from(element.childNodes).some(node => node.nodeType === Node.TEXT_NODE && node.textContent?.trim())
      if (!control && !hasText) continue
      const minimum = element.closest('[data-supplementary="true"]') ? 12 : 14
      const fontSize = parseFloat(getComputedStyle(element).fontSize)
      if (fontSize < minimum) failures.push(`font ${fontSize}<${minimum}: ${name(element)}`)
      if (window.innerWidth <= 899 && element.matches('input:not([type="checkbox"]):not([type="radio"]):not([type="file"]),select,textarea') && fontSize < 16) failures.push(`input font ${fontSize}<16: ${name(element)}`)
    }
    if (window.innerWidth <= 899) {
      const navigation = document.querySelector('.rp-shell__bottom-nav')
      if (!navigation) failures.push('mobile bottom navigation is missing')
      else for (const label of navigation.querySelectorAll('.rp-shell__nav-label')) {
        const box = label.getBoundingClientRect()
        const lineHeight = parseFloat(getComputedStyle(label).lineHeight)
        const control = label.closest('a,button')?.getBoundingClientRect()
        const icon = label.parentElement?.querySelector('svg')?.getBoundingClientRect()
        if (!visible(label) || box.height <= 0 || box.width <= 0) failures.push(`hidden navigation caption: ${name(label)}`)
        if (Number.isFinite(lineHeight) && box.height > lineHeight * 1.25) failures.push(`wrapped navigation caption: ${name(label)}`)
        if (!control || !icon || box.top < icon.bottom - 1 || box.left < control.left - 1 || box.right > control.right + 1 || box.bottom > control.bottom + 1) failures.push(`navigation caption outside its control or above icon: ${name(label)}`)
      }
      const managementSelect = document.querySelector('.rp-management-nav-select')
      const managementLinks = document.querySelector('.rp-management-nav')
      if (managementSelect && managementLinks && visible(managementSelect) && visible(managementLinks)) failures.push('management navigation is duplicated')
    }
    const controls = Array.from(document.querySelectorAll<HTMLElement>('button,a,input,select,textarea,summary,[role="button"],[role="tab"]'))
      .filter(element => visible(element)
        && !element.matches(':disabled,[aria-disabled="true"]')
        && !element.closest('[aria-hidden="true"],[hidden],[inert]')
        && getComputedStyle(element).pointerEvents !== 'none')
      .map(element => element.matches('input[type="checkbox"],input[type="radio"]')
        ? (element as HTMLInputElement).labels?.[0] ?? element
        : element)
      .filter((element, index, all) => all.indexOf(element) === index)
    for (const control of controls) {
      const hitAreas = Array.from(control.getClientRects()).filter(box => box.width > 0 && box.height > 0)
      if (!hitAreas.length || hitAreas.some(box => box.width < 43.99 || box.height < 43.99)) {
        const sizes = hitAreas.map(box => `${box.width}x${box.height}`).join(', ') || 'none'
        failures.push(`target ${sizes}: ${name(control)}`)
      }
    }

    const structuralContainer = (element: Element) => element.matches('section,article,li,fieldset,div')
    const hasBorder = (element: Element) => {
      const style = getComputedStyle(element)
      return [style.borderTopWidth, style.borderRightWidth, style.borderBottomWidth, style.borderLeftWidth]
        .some(value => parseFloat(value) > 0)
    }
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (!node.textContent?.trim()) continue
      const parent = node.parentElement
      if (!parent || !visible(parent) || parent.closest('[aria-hidden="true"],[hidden],[inert],svg,script,style,option')) continue
      let nearestBorder: Element | null = null
      for (let current: Element | null = parent; current && current !== document.body; current = current.parentElement) {
        if (visible(current) && hasBorder(current)) { nearestBorder = current; break }
      }
      if (!nearestBorder || !structuralContainer(nearestBorder)) continue
      const containerBox = nearestBorder.getBoundingClientRect()
      const range = document.createRange()
      range.selectNodeContents(node)
      for (const textBox of Array.from(range.getClientRects())) {
        if (textBox.width <= 0 || textBox.height <= 0) continue
        let left = textBox.left
        let right = textBox.right
        let top = textBox.top
        let bottom = textBox.bottom
        for (let current: Element | null = parent; current && current !== nearestBorder; current = current.parentElement) {
          const style = getComputedStyle(current)
          const clip = current.getBoundingClientRect()
          if (['auto', 'clip', 'hidden', 'scroll'].includes(style.overflowX)) {
            left = Math.max(left, clip.left)
            right = Math.min(right, clip.right)
          }
          if (['auto', 'clip', 'hidden', 'scroll'].includes(style.overflowY)) {
            top = Math.max(top, clip.top)
            bottom = Math.min(bottom, clip.bottom)
          }
        }
        if (right <= left || bottom <= top) continue
        const inset = Math.min(left - containerBox.left, containerBox.right - right, top - containerBox.top, containerBox.bottom - bottom)
        if (inset < 7.99) failures.push(`text inset ${inset}: ${name(nearestBorder)} text=${node.textContent.trim().slice(0, 70)}`)
      }
    }

    for (let left = 0; left < controls.length; left += 1) {
      const first = controls[left]
      const firstBox = first.getBoundingClientRect()
      for (let right = left + 1; right < controls.length; right += 1) {
        const second = controls[right]
        if (first.contains(second) || second.contains(first)) continue
        if (first.closest('.password-field') != null && first.closest('.password-field') === second.closest('.password-field')) continue
        const secondBox = second.getBoundingClientRect()
        const overlapWidth = Math.min(firstBox.right, secondBox.right) - Math.max(firstBox.left, secondBox.left)
        const overlapHeight = Math.min(firstBox.bottom, secondBox.bottom) - Math.max(firstBox.top, secondBox.top)
        if (overlapWidth > 1 && overlapHeight > 1) failures.push(`overlap ${name(first)} <> ${name(second)}`)
      }
    }
    return failures
  })
  expect(violations).toEqual([])
}
