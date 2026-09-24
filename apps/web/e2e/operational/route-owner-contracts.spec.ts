import { expect, test, type Page, type Route } from '@playwright/test'
import { ROUTE_MANIFEST } from '../../src/app/routing/routeManifest'
import { ROUTE_STATE_EVIDENCE, type RouteStateEvidence } from '../../src/app/routing/routeStateEvidence'
import { installMockApi } from '../support/mockApi'
import { installOperational, issue, parkNorth, userForRole } from './fixtures'
import { fixturePath, openRouteFixture } from './routeFixtures'

const owners = ROUTE_STATE_EVIDENCE.filter(item => item.fixture === 'owner-test')
async function installOwnerTransition(page: Page, method: string, targetPath: string, handler: (route: Route) => Promise<void>) {
  await page.route('**/api/**', async route => {
    const request = route.request()
    if (request.method() === method && new URL(request.url()).pathname === targetPath) await handler(route)
    else await route.fallback()
  })
}

async function exerciseAsyncOwnerState(page: Page, owner: RouteStateEvidence, driver: NonNullable<RouteStateEvidence['ownerDriver']>) {
  const endpoint = driver.endpoint!
  let intercepted = 0
  let heldRoute: Route | undefined
  const refreshesInBackground = driver.asyncFixture === 'stale-503' || driver.asyncFixture === 'denied-403'
  const actionDriven = Boolean(driver.triggerSelector)
  if (refreshesInBackground) {
    if (driver.triggerSelector && driver.fieldSelector && !driver.setupFields?.length) {
      await page.locator(driver.fieldSelector).fill(driver.fieldValue!)
      await page.locator(driver.triggerSelector).click()
      await expect(page).toHaveURL(/\/robots\/YASADR00000000447/)
      await page.goBack({ waitUntil: 'domcontentloaded' })
    }
    await expect(page.locator(driver.protectedSelector!), `${owner.caseId}: protected fixture is preloaded before the late response`).toBeVisible()
  }
  await installOwnerTransition(page, endpoint.method, endpoint.path, async route => {
    intercepted += 1
    if (endpoint.expectedBody !== undefined) {
      expect(route.request().postDataJSON(), `${owner.caseId}: exact mutation body`).toEqual(endpoint.expectedBody)
    }
    if (driver.asyncFixture === 'pending' || driver.asyncFixture === 'denied-403') {
      heldRoute = route
      return
    }
    if (driver.asyncFixture === 'empty-200') {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(endpoint.emptyBody) })
      return
    }
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ detail: driver.asyncFixture === 'denied-403' ? 'forbidden' : 'fixture_unavailable' }),
    })
  })
  let navigation: Promise<unknown> = Promise.resolve(null)
  for (const selector of driver.setupClicks ?? []) await page.locator(selector).click()
  if (driver.setupFields?.length) {
    for (const field of driver.setupFields) await page.locator(field.selector).fill(field.value)
    await page.locator(driver.triggerSelector!).click()
  } else if (driver.triggerSelector && driver.fieldSelector) {
    await page.locator(driver.fieldSelector).fill(driver.fieldValue!)
    await page.locator(driver.triggerSelector).click()
  } else if (driver.triggerSelector) {
    await page.locator(driver.triggerSelector).click()
  } else if (driver.fieldSelector) {
    await page.locator(driver.fieldSelector).fill(driver.fieldValue!)
  } else if (driver.refreshStrategy === 'reload') {
    if (driver.clearCacheBeforeReload) {
      await page.evaluate(() => window.dispatchEvent(new CustomEvent('robopark:authorization-failure', { detail: { status: 403 } })))
    }
    navigation = page.reload({ waitUntil: 'domcontentloaded' }).catch(() => null)
  } else if (refreshesInBackground && !actionDriven) {
    await page.evaluate(() => {
      const currentNow = Date.now
      Date.now = () => currentNow() + 121_000
      Math.random = () => 0
      window.dispatchEvent(new Event('focus'))
    })
  }
  if (!refreshesInBackground && !driver.triggerSelector && !driver.fieldSelector && driver.refreshStrategy !== 'reload') {
    navigation = page.reload({ waitUntil: 'domcontentloaded' }).catch(() => null)
  }
  await expect.poll(() => intercepted, { message: `${owner.caseId}: ${endpoint.method} ${endpoint.path} is controlled by its named fixture` }).toBeGreaterThan(0)
  if (driver.asyncFixture === 'pending') {
    expect(heldRoute, `${owner.caseId}: named request remains pending`).toBeTruthy()
    if (driver.pendingKeepsProtected) await expect(page.locator(driver.protectedSelector!), `${owner.caseId}: persisted protected fixture remains while its exact request is pending`).toBeVisible()
    else await expect(page.locator(driver.protectedSelector!), `${owner.caseId}: initial pending request has not exposed protected data`).toBeHidden()
    if (driver.expectedSelector !== driver.protectedSelector) await expect(page.locator(driver.expectedSelector), `${owner.caseId}: exact loading indicator is visible`).toBeVisible()
    await heldRoute!.abort('failed')
  } else if (driver.asyncFixture === 'stale-503') {
    await navigation
    await expect(page.locator(driver.protectedSelector!), `${owner.caseId}: transitioned owner exposes inspectable DOM`).toBeVisible()
    if (driver.expectedSelector !== driver.protectedSelector) await expect(page.locator(driver.expectedSelector), `${owner.caseId}: exact stale-state boundary remains visible`).toBeVisible()
  } else if (driver.asyncFixture === 'denied-403') {
    expect(heldRoute, `${owner.caseId}: denied response is held after protected data rendered`).toBeTruthy()
    if (driver.deniedKeepsUntilResponse !== false) await expect(page.locator(driver.protectedSelector!), `${owner.caseId}: protected fixture remains until the late denial resolves`).toBeVisible()
    await heldRoute!.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ detail: 'forbidden' }) })
    if (driver.deniedKeepsProtected) {
      await expect(page.locator(driver.protectedSelector!), `${owner.caseId}: local mutation denial keeps its exact draft`).toBeVisible()
      await expect(page.locator(driver.expectedSelector), `${owner.caseId}: local mutation denial renders its exact error`).toBeVisible()
    } else {
      if (driver.reloadAfterDenied) await page.reload({ waitUntil: 'domcontentloaded' })
      await expect(page.locator(driver.protectedSelector!), `${owner.caseId}: denied response removes the preloaded protected view`).toBeHidden()
      if (driver.expectedSelector !== driver.protectedSelector) await expect(page.locator(driver.expectedSelector), `${owner.caseId}: exact denied state is visible`).toBeVisible()
    }
  } else {
    await navigation
    expect(intercepted, `${owner.caseId}: named async fixture completed`).toBeGreaterThan(0)
    if (driver.completedHidesProtected) await expect(page.locator(driver.protectedSelector!), `${owner.caseId}: completed request clears its exact form`).toBeHidden()
    if (driver.completedProtectedValue !== undefined) await expect(page.locator(driver.protectedSelector!), `${owner.caseId}: completed request resets its exact field`).toHaveValue(driver.completedProtectedValue)
    if (driver.expectedSelector !== driver.protectedSelector) await expect(page.locator(driver.expectedSelector), `${owner.caseId}: exact completed state is visible`).toBeVisible()
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
          : owner.routeId === 'no-cabinet'
            ? { ...base, role: 'royal' as const, permissions: [], parks: [parkNorth] }
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
  apiRequests: string[],
) {
  const route = ROUTE_MANIFEST.find(item => item.id === owner.routeId)!
  if (route.surface === 'shell') {
    if (owner.actorRole === 'guest' || owner.actorRole === 'restricted') throw new Error(`${owner.caseId}: executable owner needs a system role`)
    if (owner.routeId === 'work-issue') {
      await installOperational(page, { user: userForRole(owner.actorRole), issue: { ...issue, claim: { park_id: 7 }, workflow: { owner: { login: 'mechanic-e2e', display: '\u041c\u0435\u0445\u0430\u043d\u0438\u043a \u0441\u043c\u0435\u043d\u044b' }, review_state: null, display_status: 'in_progress', sync_state: 'synced', has_current_cycle_comment: true } } })
      await page.goto(fixturePath(route))
    } else {
      await openRouteFixture(page, owner.routeId, userForRole(owner.actorRole))
    }
  } else {
    await openSimpleOwner(page, owner)
  }

  const configuredDriver = owner.ownerDriver!
  const driver = configuredDriver
  expect(new URL(page.url()).pathname, `${owner.caseId}: owner resolved a concrete application route`).toMatch(/^\//)

  if (driver.routeSearch) {
    await page.goto(`${new URL(page.url()).pathname}${driver.routeSearch}`)
    await expect(page.locator(owner.routeId === 'report-detail' ? '.report-detail' : 'main')).toBeVisible()
  }

  if (route.surface === 'shell') {
    expect(apiRequests.length > 0, `${owner.caseId}: loaded owner issued a domain API request`).toBeTruthy()
  }

  let actionRequests = 0
  if (owner.routeId === 'admin-settings' && (owner.stateId === 'backup' || owner.stateId === 'restore')) {
    await installOwnerTransition(page, 'GET', '/api/admin/ops/system-health', async transition => {
      await transition.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
        version: 'evidence', git_sha: 'abcdef0', generated_at: '2026-09-02T09:00:00Z', overall: 'ok', checks: [],
        update: { state: 'idle', publication: null }, last_backup: { status: 'unknown', completed_at: null },
      }) })
    })
  }
  if (driver.action !== 'async' && driver.endpoint) {
    await installOwnerTransition(page, driver.endpoint.method, driver.endpoint.path, async transition => {
      actionRequests += 1
      await transition.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(driver.endpoint!.emptyBody) })
    })
  }

  if (driver.tabName && driver.action !== 'tab') {
    await page.getByRole('tab', { name: driver.tabName, exact: true }).click()
  }
  for (const selector of driver.setupClicks ?? []) await page.locator(selector).click()
  if (driver.triggerSelector && driver.action !== 'dialog' && driver.action !== 'async') await page.locator(driver.triggerSelector).click()
  if (driver.action === 'async') {
    // Async drivers assert their exact protected target before/after the named response.
  } else if (driver.action === 'file' || driver.targetSelector.includes('input[type="file"]')) {
    await expect(page.locator(driver.targetSelector), `${owner.caseId}: exact route state target is mounted`).toBeAttached()
  } else {
    await expect(page.locator(driver.targetSelector), `${owner.caseId}: real route owner is mounted`).toBeVisible()
  }

  if (driver.action === 'tab') {
    const tab = page.getByRole('tab', { name: driver.tabName!, exact: true })
    await tab.click()
    await expect(tab, `${owner.caseId}: exact named tab becomes selected`).toHaveAttribute('aria-selected', 'true')
    await expect(page.locator(driver.expectedSelector), `${owner.caseId}: exact tab panel is visible`).toBeVisible()
  } else if (driver.action === 'button') {
    const button = page.locator(driver.targetSelector)
    await button.click()
    await expect(page.locator(driver.expectedSelector), `${owner.caseId}: exact named control owns the state`).toBeVisible()
  } else if (driver.action === 'form') {
    const field = page.locator(driver.fieldSelector!)
    const fieldType = await field.getAttribute('type')
    if (fieldType === 'checkbox' || fieldType === 'radio') {
      await field.check()
      await expect(field, `${owner.caseId}: exact named option owns the state`).toBeChecked()
    } else if (await field.evaluate(element => element.tagName === 'SELECT')) {
      await field.selectOption(driver.fieldValue!)
      await expect(field, `${owner.caseId}: exact named selector owns the state`).toHaveValue(driver.fieldValue!)
    } else {
      await field.fill(driver.fieldValue!)
      await expect(field, `${owner.caseId}: exact named field owns the draft`).toHaveValue(driver.fieldValue!)
    }
  } else if (driver.action === 'file') {
    await expect(page.locator(driver.fileSelector!), `${owner.caseId}: exact file or capture boundary is mounted`).toBeAttached()
  } else if (driver.action === 'dialog') {
    if (driver.dialogSelector!.startsWith('browser:')) {
      const expectedMessage = driver.dialogSelector!.slice('browser:'.length)
      const dialogMessage = new Promise<string>(resolve => page.once('dialog', async dialog => { resolve(dialog.message()); await dialog.dismiss() }))
      await page.locator(driver.triggerSelector!).click()
      expect(await dialogMessage, `${owner.caseId}: exact browser confirmation is opened`).toContain(expectedMessage)
    } else {
      const trigger = page.locator(driver.triggerSelector!)
      if (driver.triggerSelector!.includes('input[type="file"]')) {
        await trigger.setInputFiles({ name: 'fixture.zip', mimeType: 'application/zip', buffer: Buffer.from('fixture') })
      } else {
        await trigger.click()
      }
      await expect(page.locator(driver.dialogSelector!), `${owner.caseId}: exact trigger opens its expected dialog`).toBeVisible()
    }
  } else if (driver.action === 'async') {
    if (route.surface === 'shell') {
      await exerciseAsyncOwnerState(page, owner, driver)
    } else {
      await expect(page.locator(driver.expectedSelector), `${owner.caseId}: exact standalone state is visible`).toBeVisible()
    }
  } else {
    await expect(page.locator(driver.expectedSelector), `${owner.caseId}: exact state assertion is visible`).toBeVisible()
  }
  if (driver.action !== 'async' && driver.endpoint) {
    expect(actionRequests, `${owner.caseId}: exact ${driver.endpoint.method} ${driver.endpoint.path} mutation is executed once`).toBe(1)
    await page.unroute('**/api/**')
  }
}

test.describe.configure({ mode: 'parallel' })

for (const owner of owners) {
  test(`${owner.caseId} [Классический]`, async ({ page }) => {
    const apiRequests: string[] = []
    page.on('request', request => {
      const url = new URL(request.url())
      if (url.pathname.startsWith('/api/')) apiRequests.push(`${request.method()} ${url.pathname}`)
    })
    await exerciseOwnerBehavior(page, owner, apiRequests)
  })
}

test('owner collector resolves every delegated state to one exact Classic node id', () => {
  expect(new Set(owners.map(owner => owner.caseId)).size).toBe(owners.length)
  for (const owner of owners) {
    expect(owner.ownerTest?.title).toBe(`${owner.caseId} [Классический]`)
    expect(owner.ownerTest?.stateKey).toBe(owner.caseId)
    expect(owner.ownerDriver?.stateKey).toBe(owner.caseId)
    expect(owner.ownerDriver?.stateKind).toBe(owner.kind)
    expect(owner.ownerDriver?.targetSelector).toBeTruthy()
    expect(owner.ownerDriver?.expectedSelector).toBeTruthy()
  }
  expect(owners.filter(owner => owner.ownerDriver?.targetSelector === 'main').map(owner => owner.caseId)).toEqual([])
  expect(owners.filter(owner => /^(main|body|\[role=["']?main)/.test(owner.ownerDriver?.targetSelector ?? '')).map(owner => owner.caseId)).toEqual([])
  expect(owners.filter(owner => owner.kind === 'form' && !/(input|textarea|select|^#)/.test(owner.ownerDriver?.fieldSelector ?? '')).map(owner => owner.caseId)).toEqual([])
  expect(owners.filter(owner => owner.kind === 'file' && !/(input.*file|issue-attach|attachment|has-text|scheme-host)/.test(owner.ownerDriver?.fileSelector ?? '')).map(owner => owner.caseId)).toEqual([])
  expect(owners.filter(owner => owner.ownerDriver?.action === 'dialog' && (!owner.ownerDriver.triggerSelector || !owner.ownerDriver.dialogSelector)).map(owner => owner.caseId)).toEqual([])
  expect(owners.filter(owner => owner.ownerDriver?.action === 'tab' && (!owner.ownerDriver.tabName || !/(role="tab"|#tab-)/.test(owner.ownerDriver.targetSelector))).map(owner => owner.caseId)).toEqual([])
  expect(owners.filter(owner => owner.ownerDriver?.action === 'async' && (!owner.ownerDriver.endpoint?.path || !owner.ownerDriver.asyncFixture || !owner.ownerDriver.protectedSelector)).map(owner => owner.caseId)).toEqual([])
  expect(exerciseOwnerBehavior).toBeDefined()
  expect(exerciseAsyncOwnerState).toBeDefined()
})

test('schedule week and month owner drivers target the current calendar surfaces', () => {
  const scheduleOwners = owners.filter(owner => owner.routeId === 'schedule' && ['week', 'month'].includes(owner.stateId))

  expect(scheduleOwners.map(owner => owner.caseId).sort()).toEqual([
    'route-coverage:schedule:month',
    'route-coverage:schedule:week',
  ])
  expect(scheduleOwners.map(owner => owner.ownerDriver?.expectedSelector).sort()).toEqual([
    '.rp-schedule-calendar__days--month',
    '.rp-schedule-calendar__days--week',
  ])
})
