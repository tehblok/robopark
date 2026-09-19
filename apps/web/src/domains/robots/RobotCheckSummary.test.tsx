import { render, screen, within } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import type { EmergencySnapshot } from '../../api'
import { RobotCheckSummary } from './RobotCheckSummary'

const snapshot: EmergencySnapshot = {
  vin: 'YASADR00000001460',
  short_number: '1460',
  observed_at: '2026-09-19T09:00:00Z',
  online: true,
  speed: 0,
  charge_percent: 80,
  battery1_percent: 75,
  battery2_percent: 85,
  battery1_connected: true,
  battery2_connected: true,
  disk_percent: 20,
  mode: 'AUTO',
  icp_label: 'ICP',
  icp_ok: true,
  lte_label: 'LTE',
  lte_ok: true,
  connection: 'lte',
  sim_signals: [7000, 8400],
  error_banner: null,
  lat: null,
  lon: null,
  heading_deg: null,
  wheels_fault: [],
  diagnostic_events: [],
}

it('groups the connection type and both raw SIM readings into one compact list', () => {
  render(<RobotCheckSummary snapshot={snapshot} online failed={false} pending={false} onRefresh={vi.fn()} onShowDiagnostic={vi.fn()} />)

  const connection = screen.getByRole('list', { name: 'Параметры связи' })
  expect(within(connection).getAllByRole('listitem').map(item => item.textContent)).toEqual([
    'Соединение: Мобильное',
    'SIM 1: 7000',
    'SIM 2: 8400',
  ])
})
