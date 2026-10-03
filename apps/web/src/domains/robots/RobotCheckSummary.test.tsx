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

it('shows the operator an advisory result for both batteries without closing the task', () => {
  const current = { ...snapshot, observed_at: new Date().toISOString(), battery1_percent: 90, battery2_percent: 90 }
  const props = { online: true, failed: false, pending: false, onRefresh: vi.fn(), onShowDiagnostic: vi.fn() }
  const view = render(<RobotCheckSummary snapshot={current} {...props} />)
  expect(screen.getByText('Проверки пройдены')).toBeInTheDocument()
  expect(screen.getByText('Решение о закрытии принимает оператор')).toBeInTheDocument()

  view.rerender(<RobotCheckSummary snapshot={{ ...current, battery2_percent: 89 }} {...props} />)
  expect(screen.getByText('Требуется внимание')).toBeInTheDocument()
  expect(screen.getByText('АКБ 2 ниже 90%')).toBeInTheDocument()
})

it('does not present a charge as verified when the battery connection is unknown', () => {
  render(<RobotCheckSummary snapshot={{ ...snapshot, observed_at: new Date().toISOString(), battery1_percent: 95, battery1_connected: null }} online failed={false} pending={false} onRefresh={vi.fn()} onShowDiagnostic={vi.fn()} />)

  expect(screen.getByText('Подключение не подтверждено · 95 %')).toBeVisible()
  expect(screen.getByText('Нет данных об АКБ 1')).toBeVisible()
  expect(screen.queryByText('Проверки пройдены')).not.toBeInTheDocument()
})

it('does not display an invalid battery percentage as a measurement', () => {
  render(<RobotCheckSummary snapshot={{ ...snapshot, observed_at: new Date().toISOString(), battery1_percent: 101 }} online failed={false} pending={false} onRefresh={vi.fn()} onShowDiagnostic={vi.fn()} />)

  expect(screen.getByText('Заряд не измерен')).toBeVisible()
  expect(screen.getByText('Нет данных об АКБ 1')).toBeVisible()
  expect(screen.queryByText('101 %')).not.toBeInTheDocument()
})

it('does not offer a manual retry while the robot API requested a pause', () => {
  render(<RobotCheckSummary snapshot={{ ...snapshot, observed_at: new Date().toISOString() }} online failed pending={false} retryDeferred onRefresh={vi.fn()} onShowDiagnostic={vi.fn()} />)

  expect(screen.queryByRole('button', { name: 'Повторить проверку' })).not.toBeInTheDocument()
})
