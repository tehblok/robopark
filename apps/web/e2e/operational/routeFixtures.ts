import { expect, type Page } from '@playwright/test'
import type { Report, User } from '../../src/api'
import { ROUTE_MANIFEST, type AppRouteId, type RouteManifestItem } from '../../src/app/routing/routeManifest'
import { analyticsFixture } from '../../src/domains/analytics/analytics.test-support'
import type { MockRoute } from '../support/mockApi'
import { installOperational } from './fixtures'

const routeReport: Report = {
  id: 1, kind: 'mechanic_problem', status: 'open', park_id: 7, author_user_id: 101,
  target_role: 'operator', tracker_key: 'ROBOPARK-42', tracker_url: null,
  title: 'Проверить колесо', body: 'Робот требует осмотра.', parent_report_id: null,
  return_comment: null, created_at: '2026-09-02T09:00:00Z', updated_at: '2026-09-02T09:00:00Z', resolved_at: null,
}

function routeMockRoutes(): MockRoute[] {
  return [
    { method: 'GET', path: '/api/analytics', handler: request => {
      const params = new URL(request.url).searchParams
      return { json: analyticsFixture(Number(params.get('park_id')), Number(params.get('days')), params.get('bucket') === '2h' ? '2h' : '1d') }
    } },
    { method: 'GET', path: '/api/reports/mine', handler: () => ({ json: [routeReport] }) },
    { method: 'GET', path: '/api/reports/inbox', handler: () => ({ json: [routeReport] }) },
    { method: 'GET', path: '/api/reports/1', handler: () => ({ json: routeReport }) },
    { method: 'GET', path: '/api/reports/badge', handler: () => ({ json: { count: 1 } }) },
    { method: 'GET', path: '/api/admin/roles', handler: () => ({ json: [] }) },
    { method: 'GET', path: '/api/admin/roles/permissions/catalog', handler: () => ({ json: [] }) },
    { method: 'GET', path: '/api/admin/emergency/sections', handler: () => ({ json: [] }) },
    { method: 'GET', path: '/api/admin/park-requests', handler: () => ({ json: [] }) },
    { method: 'GET', path: '/api/admin/settings/integrations', handler: () => ({ json: {} }) },
    { method: 'GET', path: '/api/admin/settings/tracker-policy', handler: () => ({ json: {} }) },
    { method: 'GET', path: '/api/admin/settings/screenshot-guard', handler: () => ({ json: {} }) },
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

export async function openRouteFixture(page: Page, routeId: AppRouteId, user: User): Promise<void> {
  const route = ROUTE_MANIFEST.find(item => item.id === routeId)
  if (!route) throw new Error(`Unknown route fixture: ${routeId}`)
  await installOperational(page, { user, routes: routeMockRoutes() })
  await page.goto(fixturePath(route))
  await expect(page.locator('main')).toBeVisible()
  await expect(routeReadyMarker(page, routeId)).toBeVisible()
}

function routeReadyMarker(page: Page, routeId: AppRouteId) {
  switch (routeId) {
    case 'overview': return page.getByRole('heading', { name: 'Очередь внимания' })
    case 'operator-parks': return page.getByRole('heading', { name: 'Мои парки', level: 1 })
    case 'work': return page.locator('.rp-work-entities').first()
    case 'work-issue': return page.getByRole('heading', { name: 'Задача ROBOPARK-42', exact: true })
    case 'robots': return page.locator('.rp-robots-search-panel')
    case 'robot-detail': return page.getByRole('heading', { name: 'Робот 447', exact: true })
    case 'robot-check': return page.getByRole('tabpanel', { name: 'Состояние' })
    case 'legacy-robot-check': return page.locator('.rp-robots-search-panel')
    case 'campaigns': return page.getByRole('heading', { name: 'СК и оклейка', exact: true })
    case 'campaign-detail': return page.getByRole('heading', { name: 'СК и оклейка', exact: true })
    case 'reports': return page.getByRole('heading', { name: 'Репорты', exact: true })
    case 'reports-new': return page.getByRole('heading', { name: 'Создать репорт', exact: true })
    case 'report-detail': return page.getByRole('heading').first()
    case 'analytics': return page.getByRole('heading', { name: 'Динамика процесса' })
    case 'admin': return page.getByRole('heading', { name: 'Управление', exact: true, level: 1 })
    case 'admin-settings': return page.locator('.rp-management').first()
    case 'admin-users': return page.getByRole('heading', { name: 'Пользователи', exact: true })
    case 'admin-roles': return page.getByRole('heading', { name: 'Роли и доступы', exact: true })
    case 'admin-tracker': return page.getByRole('heading', { name: 'Рабочий стол Startrek', exact: true })
    case 'admin-robot-check': return page.getByRole('heading', { name: 'Настройки проверки робота', exact: true })
    default: return page.locator('main')
  }
}

export async function assertResponsiveContracts(page: Page, width: number): Promise<void> {
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
  const violations = await page.evaluate(width => {
    const failures: string[] = []
    const visible = (element: Element) => {
      const style = getComputedStyle(element)
      return element.getClientRects().length > 0 && style.visibility !== 'hidden' && style.display !== 'none'
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
      if (width <= 899 && element.matches('input:not([type="checkbox"]):not([type="radio"]):not([type="file"]),select,textarea') && fontSize < 16) failures.push(`input font ${fontSize}<16: ${name(element)}`)
    }
    if (width <= 899) {
      const navigation = document.querySelector('.rp-shell__bottom-nav')
      if (!navigation) failures.push('mobile bottom navigation is missing')
      else for (const label of navigation.querySelectorAll('.rp-shell__nav-label')) {
        const box = label.getBoundingClientRect()
        const control = label.closest('a,button')?.getBoundingClientRect()
        const icon = label.parentElement?.querySelector('svg')?.getBoundingClientRect()
        if (!visible(label) || box.height <= 0 || box.width <= 0) failures.push(`hidden navigation caption: ${name(label)}`)
        if (!control || !icon || box.top < icon.bottom - 1 || box.left < control.left - 1 || box.right > control.right + 1 || box.bottom > control.bottom + 1) failures.push(`navigation caption outside its control or above icon: ${name(label)}`)
      }
    }
    if (width === 320 || width === 390) {
      for (const element of document.querySelectorAll('button,a,input,select,textarea')) {
        if (!visible(element)) continue
        const target = element.matches('input[type="checkbox"],input[type="radio"]')
          ? (element as HTMLInputElement).labels?.[0] : element
        if (!target) { failures.push(`missing associated label: ${name(element)}`); continue }
        const box = target.getBoundingClientRect()
        if (box.width < 43.99 || box.height < 43.99) failures.push(`target ${box.width}x${box.height}: ${name(element)}`)
      }
    }
    return failures
  }, width)
  expect(violations).toEqual([])
}
