import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { ROUTE_MANIFEST } from './routeManifest'
import { ROUTE_COVERAGE_MANIFEST } from './routeCoverageManifest'

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

  it('resolves every API permission assertion to a collected pytest function', () => {
    const repoRoot = resolve(process.cwd(), '../..')
    for (const route of ROUTE_COVERAGE_MANIFEST) for (const action of route.actions) {
      for (const assertion of action.apiPermissionAssertions) {
        const [path, testName] = assertion.split('::')
        const source = readFileSync(resolve(repoRoot, path), 'utf8')
        expect(source, assertion).toContain(`def ${testName}(`)
      }
    }
  })
})
