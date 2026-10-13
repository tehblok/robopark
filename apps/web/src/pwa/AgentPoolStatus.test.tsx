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
  expect(screen.getByText('Mac оператора')).toBeVisible()
  expect(screen.queryByText('private-id')).not.toBeInTheDocument()
})

it('explains when the shared Mac pool is disabled', async () => {
  render(<AgentPoolStatus load={async () => ({ enabled: false, scope: 'own', summary: { connected: 0, ready: 0, busy: 0, paused: 0, capacity: 0, inflight: 0, queued: 0, requests: 0, completed: 0, failed: 0, encoded_bytes: 0, decoded_bytes: 0, probes_sent: 0, probes_failed: 0, upstream_dispatches: 0, upstream_backpressure: 0 }, devices: [] })} />)
  expect(await screen.findByText('Распределение запросов по Mac выключено на сервере.')).toBeVisible()
})
