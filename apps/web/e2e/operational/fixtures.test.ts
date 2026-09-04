import { describe, expect, it } from 'vitest'
import type { OperationsOverview } from '../../src/api'
import { operationalRoutes, roles, userForRole } from './fixtures'

describe('operational fixture route builder', () => {
  it.each(roles)('serves visible Operations data for %s', async (role) => {
    const route = operationalRoutes({ user: userForRole(role) }).find((candidate) => (
      candidate.method === 'GET' && candidate.path === '/api/operations/overview'
    ))

    expect(route, 'the shared builder must own the Operations endpoint').toBeDefined()
    const response = await route!.handler(new Request('http://fixture.invalid/api/operations/overview?park_id=7&status=all'))
    const overview = response.json as OperationsOverview
    expect(overview.park_id).toBe(7)
    expect(overview.tasks).toHaveLength(1)
    expect(overview.tasks[0]?.key).toBe('ROBOPARK-42')
  })
})
