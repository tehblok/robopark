import { describe, expect, it, vi } from 'vitest'
import { loadRelatedRobotWork, resolveRobotDetail, type RobotDetailApiClient } from './robotDetailData'

function client(): RobotDetailApiClient {
  const items = (key: string) => [{ key, summary: 'Робот остановился', status: 'Open', status_key: 'open', robot: '447', created_at: '2026-09-02T08:00:00Z', hours_created: '1', url: `https://st.yandex-team.ru/${key}`, bucket: 'new' }]
  return {
    emergencyResolve: vi.fn(async () => ({ vin: 'YASADR00000000447', sections: [] })),
    emergencySnapshot: vi.fn(async () => ({
      vin: 'YASADR00000000447', short_number: '447', observed_at: '2026-09-02T09:05:00Z', online: true,
      speed: 0, charge_percent: 80, battery1_percent: 80, battery2_percent: 80, disk_percent: 20,
      mode: 'AUTO', icp_label: 'ICP', icp_ok: true, lte_label: 'LTE', lte_ok: true,
      connection: 'lte' as const, error_banner: null, lat: 55, lon: 37, heading_deg: 0, wheels_fault: [],
    })),
    mechanicRobotTickets: vi.fn(async () => ({ query: 'VIN', items: items('MECHANIC-1') })),
    operatorRobotTickets: vi.fn(async () => ({ query: 'VIN', items: items('OPERATOR-1') })),
    trackerRobotTickets: vi.fn(async () => ({ query: 'VIN', items: items('TRACKER-1') })),
  }
}

describe('robot detail data', () => {
  it('resolves the identity before requesting its snapshot', async () => {
    const apiClient = client()
    const result = await resolveRobotDetail(apiClient, '447')
    expect(apiClient.emergencyResolve).toHaveBeenCalledWith('447')
    expect(apiClient.emergencySnapshot).toHaveBeenCalledWith('YASADR00000000447')
    expect(result).toMatchObject({ vin: 'YASADR00000000447', sections: [], snapshot: { vin: 'YASADR00000000447' } })
  })
  it.each([
    ['mechanic', 'MECHANIC-1'], ['operator', 'OPERATOR-1'], ['admin', 'TRACKER-1'],
    ['royal', 'TRACKER-1'], ['field_lead', 'TRACKER-1'],
  ])('uses the effective role queue for %s', async (role, key) => {
    const apiClient = client()
    expect(await loadRelatedRobotWork(apiClient, { role, permissions: ['tracker.read'] }, 'VIN')).toMatchObject([{ key }])
    const method = role === 'mechanic' ? 'mechanicRobotTickets' : role === 'operator' ? 'operatorRobotTickets' : 'trackerRobotTickets'
    expect(apiClient[method]).toHaveBeenCalledWith('VIN')
  })
  it.each(['driver', 'field_lead'])('does not broaden scope without tracker.read for %s', async (role) => {
    const apiClient = client()
    expect(await loadRelatedRobotWork(apiClient, { role, permissions: ['nav.robot_search'] }, 'VIN')).toBeNull()
    expect(apiClient.mechanicRobotTickets).not.toHaveBeenCalled()
    expect(apiClient.operatorRobotTickets).not.toHaveBeenCalled()
    expect(apiClient.trackerRobotTickets).not.toHaveBeenCalled()
  })
})
