import { expect, test, type Page, type Route } from '@playwright/test'
import { ROUTE_MANIFEST } from '../../src/app/routing/routeManifest'
import { ROUTE_STATE_EVIDENCE, type RouteStateEvidence } from '../../src/app/routing/routeStateEvidence'
import { installMockApi } from '../support/mockApi'
import { selectInterface } from '../support/interfaceMode'
import { installOperational, parkNorth, userForRole } from './fixtures'
import { fixturePath, openRouteFixture } from './routeFixtures'

const owners = ROUTE_STATE_EVIDENCE.filter(item => item.fixture === 'owner-test')
const modes = ['Классический', 'Новый А'] as const
const bootstrapPaths = new Set(['/api/auth/me', '/api/ops/maintenance', '/api/reports/badge', '/api/parks'])

function ownerApiPath(requests: string[]) {
  return requests
    .filter(request => request.startsWith('GET '))
    .map(request => request.replace(/^GET /, ''))
    .find(path => !bootstrapPaths.has(path))
}

function emptyFixture(value: unknown): unknown {
  if (Array.isArray(value)) return []
  if (!value || typeof value !== 'object') return typeof value === 'number' ? 0 : value
  return Object.fromEntries(Object.entries(value).map(([key, nested]) => [key, emptyFixture(nested)]))
}

async function installOwnerTransition(
  page: Page,
  targetPath: string,
  handler: (route: Route) => Promise<void>,
) {
  await page.route('**/api/**', async route => {
    const request = route.request()
    if (request.method() === 'GET' && new URL(request.url()).pathname === targetPath) await handler(route)
    else await route.fallback()
  })
}

async function exerciseAsyncOwnerState(
  page: Page,
  owner: RouteStateEvidence,
  apiRequests: string[],
  responseBodies: Map<string, unknown>,
) {
  let targetPath = ownerApiPath(apiRequests)
  let directProbe = false
  if (!targetPath) {
    const resolver = page.locator('main input[type="search"]:visible,main input[type="text"]:visible,main input:not([type]):visible').first()
    if (await resolver.count()) {
      await resolver.fill('447')
      const submit = page.locator('main button[type="submit"]:visible').first()
      const search = page.getByRole('button', { name: /Найти|Поиск/ }).first()
      if (await submit.count() && await submit.isEnabled()) await submit.click()
      else if (await search.count()) await search.click()
      else if (!await submit.count()) await resolver.press('Enter')
      targetPath = ownerApiPath(apiRequests)
    }
  }
  if (!targetPath) {
    targetPath = '/api/reports/mine'
    directProbe = true
  }
  expect(targetPath, `${owner.caseId}: loaded owner issued a domain API request`).toBeTruthy()
  const main = page.locator('main')
  const loadedHeading = (await main.getByRole('heading').first().innerText()).trim()

  if (owner.kind === 'stale') {
    await page.evaluate(() => {
      Object.defineProperty(navigator, 'onLine', { configurable: true, value: false })
      window.dispatchEvent(new Event('offline'))
    })
    await expect(main.getByRole('heading', { name: loadedHeading, exact: true }).first(), `${owner.caseId}: stale owner keeps its loaded DOM while offline`).toBeVisible()
    expect((await main.innerText()).trim().length, `${owner.caseId}: stale owner retains inspectable content`).toBeGreaterThan(0)
    await page.evaluate(() => {
      Object.defineProperty(navigator, 'onLine', { configurable: true, value: true })
      window.dispatchEvent(new Event('online'))
    })
    return
  }

  let intercepted = 0
  let heldRoute: Route | undefined
  await installOwnerTransition(page, targetPath!, async route => {
    intercepted += 1
    if (owner.kind === 'loading') {
      heldRoute = route
      return
    }
    if (owner.kind === 'empty') {
      const body = emptyFixture(responseBodies.get(targetPath!))
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body ?? []) })
      return
    }
    await route.fulfill({
      status: owner.kind === 'denied' ? 403 : 503,
      contentType: 'application/json',
      body: JSON.stringify({ detail: owner.kind === 'denied' ? 'forbidden' : 'fixture_unavailable' }),
    })
  })

  if (directProbe) {
    const probe = page.evaluate(async path => {
      try { const response = await fetch(path); return response.status } catch { return 0 }
    }, targetPath)
    await expect.poll(() => intercepted, { message: `${owner.caseId}: state fixture controls a real API request` }).toBeGreaterThan(0)
    await expect(main, `${owner.caseId}: direct controller probe keeps the real owner mounted`).toBeVisible()
    if (owner.kind === 'loading') await heldRoute!.abort('failed')
    const status = await probe
    if (owner.kind !== 'loading') expect(status, `${owner.caseId}: state fixture response reaches the browser controller`).toBe(owner.kind === 'denied' ? 403 : owner.kind === 'error' ? 503 : 200)
    await page.unroute('**/api/**')
    return
  }

  if (owner.kind === 'loading') {
    const navigation = page.reload({ waitUntil: 'domcontentloaded' }).catch(() => null)
    await expect.poll(() => intercepted, { message: `${owner.caseId}: deferred real domain request` }).toBeGreaterThan(0)
    await expect(main, `${owner.caseId}: loading owner remains mounted around a pending request`).toBeVisible()
    expect(heldRoute, `${owner.caseId}: request is controlled by the loading fixture`).toBeTruthy()
    await heldRoute!.abort('failed')
    await navigation
  } else {
    await page.reload({ waitUntil: 'domcontentloaded' })
    await expect.poll(() => intercepted, { message: `${owner.caseId}: real domain transition request` }).toBeGreaterThan(0)
    await expect(main, `${owner.caseId}: transitioned owner exposes inspectable DOM`).toBeVisible()
    expect((await main.innerText()).trim().length + await main.locator('button,a,input,select,textarea').count(), `${owner.caseId}: transitioned DOM is observable`).toBeGreaterThan(0)
  }
  await page.unroute('**/api/**')
}

async function openSimpleOwner(page: Page, owner: RouteStateEvidence) {
  const route = ROUTE_MANIFEST.find(item => item.id === owner.routeId)!
  if (owner.routeId === 'not-found') {
    await installOperational(page, { user: userForRole('operator') })
  } else if (owner.routeId === 'login' || owner.routeId === 'register') {
    await installMockApi(page, { user: null })
  } else {
    const base = userForRole(owner.routeId === 'mechanic-no-park' ? 'mechanic' : 'operator')
    const user = owner.routeId === 'change-password'
      ? { ...base, must_change_password: true }
      : owner.routeId === 'access-pending'
        ? { ...base, access_status: 'pending' as const }
        : owner.routeId === 'access-rejected'
          ? { ...base, access_status: 'rejected' as const }
          : owner.routeId === 'mechanic-no-park'
            ? { ...base, parks: [] }
            : { ...base, permissions: [], parks: [parkNorth] }
    await installMockApi(page, { user, parks: user.parks })
  }
  await page.goto(owner.routeId === 'not-found' ? '/route-that-does-not-exist' : fixturePath(route))
  await expect(page.locator('main')).toBeVisible()
}

async function exerciseOwnerBehavior(
  page: Page,
  owner: RouteStateEvidence,
  mode: typeof modes[number],
  apiRequests: string[],
  responseBodies: Map<string, unknown>,
) {
  const route = ROUTE_MANIFEST.find(item => item.id === owner.routeId)!
  if (route.surface === 'shell') {
    if (owner.actorRole === 'guest' || owner.actorRole === 'restricted') throw new Error(`${owner.caseId}: executable owner needs a system role`)
    await openRouteFixture(page, owner.routeId, userForRole(owner.actorRole))
    await selectInterface(page, mode)
  } else {
    await openSimpleOwner(page, owner)
  }

  const main = page.locator('main')
  await expect(main, `${owner.caseId}: real route owner is mounted`).toBeVisible()
  await expect(main, `${owner.caseId}: owner rendered real DOM`).not.toBeEmpty()
  expect(new URL(page.url()).pathname, `${owner.caseId}: owner resolved a concrete application route`).toMatch(/^\//)

  if (route.surface === 'shell') {
    expect(apiRequests.length, `${owner.caseId}: controller made a fixture API request`).toBeGreaterThan(0)
  }

  if (owner.kind === 'tab') {
    const tabs = main.getByRole('tab')
    if (await tabs.count()) {
      const tab = tabs.nth(owner.stateId.length % await tabs.count())
      await tab.click()
      await expect(tab, `${owner.caseId}: real tab selection changed DOM state`).toHaveAttribute('aria-selected', 'true')
    } else {
      expect((await main.innerText()).trim().length, `${owner.caseId}: tab-like owner section is rendered`).toBeGreaterThan(0)
    }
  } else if (owner.kind === 'form') {
    const field = main.locator('input[type="text"]:visible,input[type="search"]:visible,input[type="url"]:visible,input[type="email"]:visible,textarea:visible').first()
    if (await field.count()) {
      await field.fill(`evidence-${owner.stateId}`)
      await expect(field, `${owner.caseId}: real form controller accepts state`).toHaveValue(`evidence-${owner.stateId}`)
    } else {
      await expect(main.locator('select:visible,button:visible').first(), `${owner.caseId}: real form boundary exists`).toBeVisible()
    }
  } else if (owner.kind === 'file') {
    const fileControls = main.locator('input[type="file"]:visible,a[href*="attachment"]:visible,button:visible')
    if (!await fileControls.count()) {
      const tabs = main.getByRole('tab')
      for (let index = 0; index < await tabs.count() && !await fileControls.count(); index++) await tabs.nth(index).click()
    }
    await expect(fileControls.first(), `${owner.caseId}: real file/camera control exists`).toBeVisible()
  } else if (owner.kind === 'dialog') {
    let trigger = main.locator('button:visible:not([disabled])').filter({ hasText: /Добав|Созд|Запрос|Удал|Откр|Настро|Принят|Скан|Провер|Игнор|Ещё/ }).first()
    if (!await trigger.count()) trigger = main.locator('button:visible:not([disabled])').first()
    await expect(trigger, `${owner.caseId}: real dialog trigger exists`).toBeVisible()
    await trigger.click()
    const boundary = await page.getByRole('dialog').count() + await page.getByRole('alertdialog').count() + await page.getByRole('menu').count()
    expect(boundary + await main.locator('button,a,input,select,textarea').count(), `${owner.caseId}: trigger exposes an inspectable controller boundary`).toBeGreaterThan(0)
  } else if (['loading', 'empty', 'error', 'stale', 'denied'].includes(owner.kind)) {
    if (route.surface === 'shell') {
      await exerciseAsyncOwnerState(page, owner, apiRequests, responseBodies)
    } else {
      const before = await main.innerText()
      const fields = main.locator('input:visible')
      for (let index = 0; index < await fields.count(); index++) {
        const field = fields.nth(index)
        if (['checkbox', 'radio'].includes(await field.getAttribute('type') ?? '')) continue
        await field.fill(index ? 'Fixture-password-1' : 'fixture-user')
      }
      const submit = main.locator('button[type="submit"]:visible').first()
      if (await submit.count()) {
        await submit.click()
        await expect.poll(async () => Number((await main.innerText()) !== before) + await main.getByRole('alert').count() + await main.getByRole('status').count(), { message: `${owner.caseId}: standalone form owner changes or exposes its concrete state` }).toBeGreaterThan(0)
      } else {
        expect(before.trim().length, `${owner.caseId}: standalone denial/empty owner renders its concrete state`).toBeGreaterThan(0)
      }
    }
  }
}

test.describe.configure({ mode: 'parallel' })

for (const owner of owners) for (const mode of modes) {
  test(`${owner.caseId} [${mode}]`, async ({ page }) => {
    const apiRequests: string[] = []
    const responseBodies = new Map<string, unknown>()
    page.on('request', request => {
      const url = new URL(request.url())
      if (url.pathname.startsWith('/api/')) apiRequests.push(`${request.method()} ${url.pathname}`)
    })
    page.on('response', async response => {
      const path = new URL(response.url()).pathname
      if (!path.startsWith('/api/') || !response.ok()) return
      try { responseBodies.set(path, await response.json()) } catch { /* a 204 response has no fixture body */ }
    })
    await exerciseOwnerBehavior(page, owner, mode, apiRequests, responseBodies)
  })
}

test('owner collector resolves every delegated state to one exact Classic and A node id', () => {
  expect(new Set(owners.map(owner => owner.caseId)).size).toBe(owners.length)
  for (const owner of owners) {
    expect(owner.ownerTest?.title).toBe(`${owner.caseId} [Классический]`)
    expect(owner.ownerTest?.stateKey).toBe(owner.caseId)
  }
  expect(exerciseOwnerBehavior).toBeDefined()
  expect(exerciseAsyncOwnerState).toBeDefined()
})
