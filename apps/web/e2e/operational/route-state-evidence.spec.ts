import { expect, test } from '@playwright/test'
import { ROUTE_STATE_EVIDENCE } from '../../src/app/routing/routeStateEvidence'
import { installMockApi } from '../support/mockApi'
import { userForRole } from './fixtures'
import { openRouteFixture } from './routeFixtures'

const executable = ROUTE_STATE_EVIDENCE.filter(item => item.fixture !== 'not-applicable')

test.describe.configure({ mode: 'parallel' })

for (const evidence of executable) {
  test(`${evidence.caseId} [${evidence.auth}:${evidence.actorRole}:${evidence.fixture}]`, async ({ page }) => {
    if (evidence.auth === 'unauthenticated') {
      await installMockApi(page, { user: null })
      await page.goto(evidence.routeId === 'register' ? '/register' : '/login')
    } else {
      if (evidence.actorRole === 'guest' || evidence.actorRole === 'restricted') {
        throw new Error(`${evidence.caseId} does not name an executable system role`)
      }
      await openRouteFixture(page, evidence.routeId, userForRole(evidence.actorRole))
    }

    if (evidence.trigger) {
      await page.getByRole(evidence.trigger.role, { name: evidence.trigger.name, exact: true }).first().click()
    }
    if (evidence.assertion.kind === 'url') {
      await expect(page, evidence.assertion.description).toHaveURL(new RegExp(evidence.selector))
    } else {
      await expect(page.locator(evidence.selector).first(), evidence.assertion.description).toBeVisible()
    }
  })
}

test('collector emits exactly one Playwright test per executable evidence case', () => {
  expect(new Set(executable.map(item => item.caseId)).size).toBe(executable.length)
})
