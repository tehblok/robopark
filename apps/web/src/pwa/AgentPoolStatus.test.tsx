import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { AgentPoolStatus } from './AgentPoolStatus'

it('shows connected capacity and work distribution without exposing device ids', async () => {
  render(<AgentPoolStatus load={async () => ({
    enabled: true, scope: 'own', summary: { connected: 12, ready: 9, busy: 2, paused: 1, capacity: 18, inflight: 3, queued: 2, requests: 42, completed: 40, failed: 2, encoded_bytes: 100, decoded_bytes: 500, probes_sent: 4, probes_failed: 1, upstream_dispatches: 8, upstream_backpressure: 1 },
    devices: [{ id: 'private-id', label: 'Mac оператора', status: 'ready', connected: true, ready: true, capacity: 2, inflight: 1, queued: 2, load: 0.5, latency_ms: 24, power: 'ac', last_seen_at: null, requests: 10, completed: 9, failed: 1, encoded_bytes: 50, decoded_bytes: 200, upstream_dispatches: 3, upstream_backpressure: 1 }],
  })} />)
  expect(await screen.findByText('9 готово из 12 подключённых · 3 из 18 слотов занято')).toBeVisible()
  expect(screen.getByText('2 в очереди · 8 запросов к Emergency · 1 ожиданий лимита')).toBeVisible()
  expect(screen.getAllByText('Mac оператора')[0]).toBeVisible()
  expect(screen.queryByText('private-id')).not.toBeInTheDocument()
})

it('explains when the shared Mac pool is disabled', async () => {
  render(<AgentPoolStatus load={async () => ({ enabled: false, scope: 'own', summary: { connected: 0, ready: 0, busy: 0, paused: 0, capacity: 0, inflight: 0, queued: 0, requests: 0, completed: 0, failed: 0, encoded_bytes: 0, decoded_bytes: 0, probes_sent: 0, probes_failed: 0, upstream_dispatches: 0, upstream_backpressure: 0 }, devices: [] })} />)
  expect(await screen.findByText('Распределение запросов по Mac выключено на сервере.')).toBeVisible()
})

it('renders an accessible employee and Mac network graph with live resource metrics', async () => {
  render(<AgentPoolStatus load={async () => ({
    enabled: true,
    scope: 'all',
    summary: { connected: 1, ready: 1, busy: 0, paused: 0, capacity: 2, inflight: 1, queued: 0, requests: 8, completed: 7, failed: 1, encoded_bytes: 250000, decoded_bytes: 1000000, probes_sent: 2, probes_failed: 0, upstream_dispatches: 7, upstream_backpressure: 0, cache_hits: 3, compression_saved_bytes: 750000 },
    devices: [{
      id: 'mac-graph-id', label: 'MacBook Air', status: 'ready', connected: true, ready: true, fresh: true,
      capacity: 2, inflight: 1, queued: 0, load: 0.42, latency_ms: 18, response_latency_ms: 240,
      throughput_mbps: 8, last_success_age_seconds: 4, cpu_percent: 42.5, memory_percent: 61,
      thermal_state: 'fair', power: 'ac', last_seen_at: '2026-10-14T12:00:00Z', requests: 8,
      completed: 7, failed: 1, encoded_bytes: 250000, decoded_bytes: 1000000, cache_hits: 3,
      compression_saved_bytes: 750000, upstream_dispatches: 7, upstream_backpressure: 0,
      owner_user_id: 11, owner_username: 'operator', owner_display_name: 'Alex Operator', probes_sent: 2, probes_failed: 0,
    }],
  })} />)

  expect(await screen.findByRole('img', { name: 'Граф сети: сервер, сотрудники и их Mac' })).toBeVisible()
  expect(screen.getAllByText('Alex Operator')[0]).toBeVisible()
  expect(screen.getByText(/8 Мбит\/с/)).toBeVisible()
  expect(screen.getByText(/CPU 43%/)).toBeVisible()
  expect(screen.getByText(/память 61%/)).toBeVisible()
  expect(screen.getByText(/экономия 750 КБ/)).toBeVisible()
  expect(screen.queryByText('mac-graph-id')).not.toBeInTheDocument()
})

it('shows disconnected registered Macs and does not claim stale telemetry is current', async () => {
  render(<AgentPoolStatus load={async () => ({
    enabled: true,
    scope: 'own',
    summary: { connected: 0, ready: 0, busy: 0, paused: 0, capacity: 0, inflight: 0, queued: 0, requests: 0, completed: 0, failed: 0, encoded_bytes: 0, decoded_bytes: 0, probes_sent: 0, probes_failed: 0, upstream_dispatches: 0, upstream_backpressure: 0, cache_hits: 0, compression_saved_bytes: 0 },
    devices: [{
      id: 'offline-id', label: 'Mac offline', status: 'offline', connected: false, ready: false, fresh: false,
      capacity: 0, inflight: 0, queued: 0, load: 0, latency_ms: null, response_latency_ms: null,
      throughput_mbps: null, last_success_age_seconds: null, cpu_percent: null, memory_percent: null,
      thermal_state: null, power: 'unknown', last_seen_at: null, requests: 0, completed: 0, failed: 0,
      encoded_bytes: 0, decoded_bytes: 0, cache_hits: 0, compression_saved_bytes: 0,
      upstream_dispatches: 0, upstream_backpressure: 0, owner_user_id: 1, owner_username: 'owner',
      owner_display_name: 'Owner', probes_sent: 0, probes_failed: 0,
    }],
  })} />)

  expect((await screen.findAllByText('Mac offline'))[0]).toBeVisible()
  expect(screen.getAllByText(/не в сети/)[0]).toBeInTheDocument()
  expect(screen.queryByText(/CPU/)).not.toBeInTheDocument()
  expect(screen.getByText('Измерений пока нет.')).toBeVisible()
})

it('lays out twelve employee rows without overlapping the graph viewport', async () => {
  const devices = Array.from({ length: 12 }, (_, index) => ({
    id: `device-${index}`, label: `Mac ${index}`, status: 'offline' as const, connected: false, ready: false, fresh: false,
    capacity: 0, inflight: 0, queued: 0, load: 0, latency_ms: null, power: 'unknown' as const,
    last_seen_at: null, requests: 0, completed: 0, failed: 0, encoded_bytes: 0, decoded_bytes: 0,
    upstream_dispatches: 0, upstream_backpressure: 0, owner_user_id: index, owner_display_name: `Employee ${index}`,
  }))
  const { container } = render(<AgentPoolStatus load={async () => ({
    enabled: true, scope: 'all', summary: { connected: 0, ready: 0, busy: 0, paused: 0, capacity: 0, inflight: 0, queued: 0, requests: 0, completed: 0, failed: 0, encoded_bytes: 0, decoded_bytes: 0, probes_sent: 0, probes_failed: 0, upstream_dispatches: 0, upstream_backpressure: 0 }, devices,
  })} />)

  const graph = await screen.findByRole('img', { name: 'Граф сети: сервер, сотрудники и их Mac' })
  expect(Number(graph.getAttribute('viewBox')?.split(' ')[3])).toBeGreaterThanOrEqual(560)
  const ownerRows = Array.from(container.querySelectorAll('.rp-agent-graph__owner')).map(node => Number(node.getAttribute('y')))
  expect(new Set(ownerRows).size).toBe(12)
  expect(ownerRows.slice(1).every((row, index) => row - ownerRows[index] >= 40)).toBe(true)
  expect(Number(container.querySelector('.rp-agent-graph__server')?.getAttribute('y'))).toBeLessThan(150)
})

it('keeps a large employee group connection visible at the top of the scroll viewport', async () => {
  const devices = Array.from({ length: 12 }, (_, index) => ({
    id: `group-device-${index}`, label: `Group Mac ${index}`, status: 'offline' as const,
    connected: false, ready: false, fresh: false, capacity: 0, inflight: 0, queued: 0, load: 0,
    latency_ms: null, power: 'unknown' as const, last_seen_at: null, requests: 0, completed: 0,
    failed: 0, encoded_bytes: 0, decoded_bytes: 0, upstream_dispatches: 0, upstream_backpressure: 0,
    owner_user_id: 1, owner_display_name: 'Employee with many Macs',
  }))
  const { container } = render(<AgentPoolStatus load={async () => ({
    enabled: true, scope: 'all', summary: { connected: 0, ready: 0, busy: 0, paused: 0, capacity: 0, inflight: 0, queued: 0, requests: 0, completed: 0, failed: 0, encoded_bytes: 0, decoded_bytes: 0, probes_sent: 0, probes_failed: 0, upstream_dispatches: 0, upstream_backpressure: 0 }, devices,
  })} />)

  await screen.findByRole('img', { name: 'Граф сети: сервер, сотрудники и их Mac' })
  expect(Number(container.querySelector('.rp-agent-graph__owner')?.getAttribute('y'))).toBeLessThan(150)
})
