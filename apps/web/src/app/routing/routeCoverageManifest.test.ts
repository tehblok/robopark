import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { ROUTE_MANIFEST } from './routeManifest'
import { ROUTE_COVERAGE_MANIFEST } from './routeCoverageManifest'
import { ROUTE_STATE_EVIDENCE } from './routeStateEvidence'

describe('executable route coverage manifest', () => {
  it('covers every reachable route exactly once', () => {
    expect(ROUTE_COVERAGE_MANIFEST.map(item => item.routeId).sort()).toEqual(ROUTE_MANIFEST.map(route => route.id).sort())
    expect(new Set(ROUTE_COVERAGE_MANIFEST.map(item => item.routeId)).size).toBe(ROUTE_MANIFEST.length)
  })

  it('does not allow an unreviewed interface A fallback', () => {
    const incomplete = ROUTE_COVERAGE_MANIFEST
      .filter(item => ROUTE_MANIFEST.find(route => route.id === item.routeId)?.surface === 'shell')
      .filter(item => !item.interfaceAReviewed)
      .map(item => item.routeId)
    expect(incomplete).toEqual([])
  })

  it('names executable evidence for every nested state', () => {
    for (const route of ROUTE_COVERAGE_MANIFEST) {
      const shell = ROUTE_MANIFEST.find(item => item.id === route.routeId)?.surface === 'shell'
      if (shell) expect(route.classicComponent, route.routeId).not.toBe(route.interfaceAComponent)
      for (const state of route.states) expect(state.testId, `${route.routeId}:${state.id}`).toMatch(/\S/)
    }
  })

  it('covers async, stale and denied states for every shell route', () => {
    for (const route of ROUTE_COVERAGE_MANIFEST) {
      if (ROUTE_MANIFEST.find(item => item.id === route.routeId)?.surface !== 'shell') continue
      const kinds = new Set(route.states.map(state => state.kind))
      for (const kind of ['loading', 'empty', 'error', 'stale', 'denied']) {
        expect(kinds.has(kind as never), `${route.routeId}:${kind}`).toBe(true)
      }
    }
  })

  it('requires an API permission assertion for every role-visible action', () => {
    for (const route of ROUTE_COVERAGE_MANIFEST) for (const action of route.actions) {
      expect(action.roles.length, `${route.routeId}:${action.id}:roles`).toBeGreaterThan(0)
      expect(action.apiPermissionAssertions.length, `${route.routeId}:${action.id}:permissions`).toBeGreaterThan(0)
      for (const assertion of action.apiPermissionAssertions) {
        expect(assertion, `${route.routeId}:${action.id}`).toMatch(/^apps\/api\/tests\/test_[^:]+\.py::test_/)
      }
    }
  })

  it('requires an auditable reason for every not-applicable evidence case', () => {
    for (const item of ROUTE_STATE_EVIDENCE.filter(item => item.fixture === 'not-applicable')) {
      expect(item.notApplicableReason?.length, item.caseId).toBeGreaterThan(30)
      expect(item.selector, item.caseId).toBe('')
      expect(item.ownerTest?.stateKey, `${item.caseId}: exact owner state`).toBe(item.caseId)
      expect(item.ownerContract, `${item.caseId}: owner contract`).toContain(item.caseId)
    }
  })

  it('resolves every delegated state to one exact existing owner test', () => {
    const repoRoot = resolve(process.cwd(), '../..')
    for (const item of ROUTE_STATE_EVIDENCE.filter(item => item.fixture === 'owner-test')) {
      expect(item.ownerContract?.length, item.caseId).toBeGreaterThan(40)
      expect(item.ownerTest?.stateKey, item.caseId).toBe(item.caseId)
      expect(item.ownerContract, item.caseId).toContain(item.caseId)
      expect(item.ownerContract, item.caseId).toContain(`${item.routeId} ${item.kind} state`)
      const source = readFileSync(resolve(repoRoot, item.ownerTest!.path), 'utf8')
      expect(item.ownerTest?.path, item.caseId).toBe('apps/web/e2e/operational/route-owner-contracts.spec.ts')
      expect(item.ownerTest?.title, item.caseId).toBe(`${item.caseId} [Классический]`)
      expect(source, `${item.caseId}: dynamically collected exact node ids`).toContain('test(`${owner.caseId} [${mode}]`')
      expect(source, `${item.caseId}: real owner behavior assertion`).toContain('exerciseOwnerBehavior(page, owner, mode')
      expect(source, `${item.caseId}: mounted owner assertion cannot be removed`).toContain('real route owner is mounted`).toBeVisible()')
      expect(source, `${item.caseId}: domain request assertion cannot be removed`).toContain('loaded owner issued a domain API request`).toBeTruthy()')
      expect(source, `${item.caseId}: async transition assertion cannot be removed`).toContain('transitioned owner exposes inspectable DOM`).toBeVisible()')
    }
  })

  it('has an exact one-to-one evidence case for every declared nested state', () => {
    const declared = ROUTE_COVERAGE_MANIFEST.flatMap(route => route.states.map(state => state.testId)).sort()
    const evidenced = ROUTE_STATE_EVIDENCE.map(item => item.caseId).sort()

    expect(new Set(evidenced).size, 'duplicate route-state evidence case ids').toBe(evidenced.length)
    expect(evidenced).toEqual(declared)
    for (const evidence of ROUTE_STATE_EVIDENCE) {
      const state = ROUTE_COVERAGE_MANIFEST.find(route => route.routeId === evidence.routeId)
        ?.states.find(item => item.id === evidence.stateId)
      expect(state?.kind, evidence.caseId).toBe(evidence.kind)
      expect(evidence.assertion?.description?.trim().length, `${evidence.caseId}: concrete assertion`).toBeGreaterThan(10)
      if (!['not-applicable', 'owner-test'].includes(evidence.fixture)) {
        expect(evidence.selector.trim().length, `${evidence.caseId}: selector`).toBeGreaterThan(0)
      }
    }
  })

  it('has one explicit allow or deny API decision for every action and route audience', () => {
    const assertionOwners = new Set<string>()
    for (const route of ROUTE_COVERAGE_MANIFEST) for (const action of route.actions) {
      const decisions = action.permissionEvidence ?? []
      expect(new Set(decisions.map(item => item.role)).size, `${route.routeId}:${action.id}: duplicate roles`).toBe(decisions.length)
      const expectedRoles = route.roles.includes('guest')
        ? [...route.roles]
        : ['royal', 'admin', 'operator', 'mechanic', 'driver', 'restricted']
      expect(decisions.map(item => item.role).sort(), `${route.routeId}:${action.id}: role matrix`).toEqual(expectedRoles.sort())
      for (const decision of decisions) {
        expect(decision.outcome, `${route.routeId}:${action.id}:${decision.role}`).toBe(
          action.roles.includes(decision.role) ? 'allow' : 'deny',
        )
        expect(decision.apiPermissionAssertion, `${route.routeId}:${action.id}:${decision.role}`).toMatch(
          /^apps\/api\/tests\/test_[^:]+\.py::test_[^[]+(?:\[[^\]]+\])?$/,
        )
        const parameterId = decision.apiPermissionAssertion.match(/\[([^\]]+)\]$/)?.[1]
        expect(parameterId, `${route.routeId}:${action.id}:${decision.role}: exact parameter`).toBe(
          `${action.id}-${decision.role}-${decision.outcome}`,
        )
        expect(assertionOwners.has(decision.apiPermissionAssertion), `reused action evidence: ${decision.apiPermissionAssertion}`).toBe(false)
        assertionOwners.add(decision.apiPermissionAssertion)
      }
    }
  })

  it('matches inventory action visibility to the API permission matrix', () => {
    const inventory = ROUTE_COVERAGE_MANIFEST.find(route => route.routeId === 'inventory')!
    const stock = inventory.actions.find(action => action.id === 'stock-settings')!
    const documents = inventory.actions.find(action => action.id === 'receive-and-inventory')!
    const exports = inventory.actions.find(action => action.id === 'labels-and-export')!
    const catalog = inventory.actions.find(action => action.id === 'catalog-delete-or-merge')!
    expect(stock.roles).toEqual(['royal', 'admin', 'mechanic', 'restricted'])
    expect(documents.roles).toEqual(['royal', 'admin', 'mechanic', 'restricted'])
    expect(exports.roles).toEqual(['royal', 'admin', 'mechanic', 'restricted'])
    expect(catalog.roles).toEqual(['royal', 'admin', 'restricted'])
    for (const decision of [...stock.permissionEvidence, ...documents.permissionEvidence, ...exports.permissionEvidence, ...catalog.permissionEvidence]) {
      expect(decision.apiPermissionAssertion).toContain('test_inventory_action_role_matrix[')
    }
  })

  it('resolves every API permission assertion to a collected pytest function', () => {
    const repoRoot = resolve(process.cwd(), '../..')
    for (const route of ROUTE_COVERAGE_MANIFEST) for (const action of route.actions) {
      const assertions = new Set([
        ...action.apiPermissionAssertions,
        ...action.permissionEvidence.map(item => item.apiPermissionAssertion),
      ])
      for (const assertion of assertions) {
        const [path, collectedName] = assertion.split('::')
        const parameterId = collectedName.match(/\[([^\]]+)\]$/)?.[1]
        const testName = collectedName.replace(/\[.*\]$/, '')
        const source = readFileSync(resolve(repoRoot, path), 'utf8')
        expect(source, assertion).toContain(`def ${testName}(`)
        if (parameterId) {
          const actionId = parameterId.replace(/-(royal|admin|operator|mechanic|driver|restricted|guest)-(allow|deny)$/, '')
          expect(source, `${assertion}: action is registered by the HTTP matrix`).toContain(`"${actionId}"`)
          if (path.endsWith('test_inventory_catalog.py')) {
            expect(source, `${assertion}: literal collected parameter id`).toContain(`id="${parameterId}"`)
          } else {
            expect(source, `${assertion}: outcome is part of the collected parameter id`).toContain('outcome')
          }
        }
      }
    }
  })
})
