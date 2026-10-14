import { act, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { ComputePoolStatus as PoolStatus } from '../api'
import { ComputePoolStatus } from './ComputePoolStatus'

const status: PoolStatus = {
  enabled: true, scope: 'all',
  summary: { connected: 2, ready: 1, capacity: 2, inflight: 1, active_jobs: 1, completed: 6, failed: 1, direct_input_bytes: 2_000_000, direct_output_bytes: 500_000, signaling_bytes: 12_000, direct_completed: 4, direct_failed: 1, direct_cancelled: 0, direct_retries: 1, direct_fallback: 0 },
  devices: [],
}
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

it('separates direct traffic from server signaling without claiming all uploads bypass it', async () => {
  render(<ComputePoolStatus load={async () => status} />)
  expect(await screen.findByText('1 свободно из 2 подключённых · 1 из 2 слотов занято')).toBeVisible()
  expect(screen.getByText('Напрямую между устройствами: 2.5 МБ · согласование через сервер: 12 КБ')).toBeVisible()
  expect(screen.getByText(/итоговые данные сохраняются на сервере/)).toBeVisible()
})

it('does not poll offline and resumes when online', async () => {
  vi.useFakeTimers()
  const online = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
  const load = vi.fn(async () => status)
  render(<ComputePoolStatus load={load} />)
  await act(() => vi.advanceTimersByTimeAsync(60_000))
  expect(load).not.toHaveBeenCalled()
  online.mockReturnValue(true)
  await act(async () => window.dispatchEvent(new Event('online')))
  expect(load).toHaveBeenCalledTimes(1)
  await act(() => vi.advanceTimersByTimeAsync(5_000))
  expect(load).toHaveBeenCalledTimes(2)
})

it('keeps one request in flight and stops polling after unmount', async () => {
  vi.useFakeTimers()
  let resolve!: (value: PoolStatus) => void
  const load = vi.fn(() => new Promise<PoolStatus>(done => { resolve = done }))
  const view = render(<ComputePoolStatus load={load} />)
  await act(async () => { window.dispatchEvent(new Event('online')); await vi.advanceTimersByTimeAsync(60_000) })
  expect(load).toHaveBeenCalledTimes(1)
  view.unmount()
  await act(async () => { resolve(status); await vi.advanceTimersByTimeAsync(60_000) })
  expect(load).toHaveBeenCalledTimes(1)
})

it('shows operation-specific latency without treating one unavailable service as a lost Mac', async () => {
  const device: PoolStatus['devices'][number] = {
    id: 'mac-1', label: 'Mac оператора', owner_user_id: 1, owner_username: 'operator', owner_display_name: null,
    status: 'ready', connected: true, ready: true, capacity: 1, inflight: 0, active_jobs: 0,
    cpu_percent: 10, memory_percent: 20, thermal_state: 'normal', power: 'ac', version: 'test',
    completed: 4, failed: 1, supports: ['corporate_tracker_read_v1'], corporate_read_capacity: 2,
    corporate_inventory_status: 'denied',
    direct_input_bytes: 0, direct_output_bytes: 0, signaling_bytes: 0, direct_completed: 0, direct_failed: 0,
    direct_cancelled: 0, direct_retries: 0, direct_fallback: 0,
    route_health: {
      'tracker:issue_read': { completed: 4, transport_failures: 0, latency_ms: 120, throughput_bytes_per_second: 10000, retry_after_seconds: 0 },
      'inventory:rover_card': { completed: 0, transport_failures: 1, latency_ms: null, throughput_bytes_per_second: null, retry_after_seconds: 10 },
    },
  }
  render(<ComputePoolStatus load={async () => ({ ...status, devices: [device] })} />)
  expect(await screen.findByText(/Tracker: личный вход подтверждён/)).toBeVisible()
  expect(screen.getByText('Задачи Tracker: 120 мс')).toBeVisible()
  expect(screen.getByText(/Карточки Inventory: время ответа ещё не измерено/)).toBeVisible()
  expect(screen.getByText(/доступ сервера к каждой системе проверяется/)).toBeVisible()
})
