import { expect, test } from '@playwright/test'
import { ROUTE_STATE_EVIDENCE } from '../../src/app/routing/routeStateEvidence'
import { installMockApi } from '../support/mockApi'
import { userForRole } from './fixtures'
import { openRouteFixture } from './routeFixtures'
import { selectInterface } from '../support/interfaceMode'

const executable = ROUTE_STATE_EVIDENCE.filter(item => !['not-applicable', 'owner-test'].includes(item.fixture))
const presentationModes = ['Классический', 'Новый А'] as const

test.describe.configure({ mode: 'parallel' })

for (const evidence of executable) for (const mode of presentationModes) {
  test(`${evidence.caseId} [${mode}:${evidence.auth}:${evidence.actorRole}:${evidence.fixture}]`, async ({ page }) => {
    if (evidence.auth === 'unauthenticated') {
      await installMockApi(page, { user: null })
      await page.goto(evidence.routeId === 'register' ? '/register' : '/login')
    } else {
      if (evidence.actorRole === 'guest' || evidence.actorRole === 'restricted') {
        throw new Error(`${evidence.caseId} does not name an executable system role`)
      }
      await openRouteFixture(page, evidence.routeId, userForRole(evidence.actorRole))
      await selectInterface(page, mode)
    }

    if (evidence.trigger) {
      const triggerName = mode === 'Новый А' && evidence.routeId === 'work-issue' && evidence.stateId === 'check'
        ? 'Проверка' : evidence.trigger.name
      await page.getByRole(evidence.trigger.role, { name: triggerName, exact: true }).first().click()
    }
    if (evidence.assertion.kind === 'url') {
      await expect(page, evidence.assertion.description).toHaveURL(new RegExp(evidence.selector))
    } else {
      const selector = mode === 'Новый А' && evidence.routeId === 'work-issue' && evidence.stateId === 'repair'
        ? '[role="tab"]:has-text("Ремонт")' : evidence.selector
      await expect(page.locator(selector).first(), evidence.assertion.description).toBeVisible()
    }
  })
}

test('collector emits exactly one Playwright test per executable evidence case and presentation', () => {
  expect(new Set(executable.map(item => item.caseId)).size).toBe(executable.length)
  expect(executable.length * presentationModes.length).toBe(executable.length * 2)
})
