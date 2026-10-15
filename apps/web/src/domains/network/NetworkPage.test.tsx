import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import type { AgentPoolStatus, ComputePoolStatus } from '../../api'
import { NetworkPage } from './NetworkPage'

const emptyAgents: AgentPoolStatus = {
  enabled: true,
  scope: 'all',
  summary: {
    connected: 0, ready: 0, busy: 0, paused: 0, capacity: 0, inflight: 0, queued: 0,
    requests: 0, completed: 0, failed: 0, encoded_bytes: 0, decoded_bytes: 0,
    probes_sent: 0, probes_failed: 0, upstream_dispatches: 0, upstream_backpressure: 0,
  },
  devices: [],
}

const emptyCompute: ComputePoolStatus = {
  enabled: true,
  scope: 'all',
  summary: {
    connected: 0, ready: 0, capacity: 0, inflight: 0, active_jobs: 0, completed: 0, failed: 0,
    direct_input_bytes: 0, direct_output_bytes: 0, signaling_bytes: 0, direct_completed: 0,
    direct_failed: 0, direct_cancelled: 0, direct_retries: 0, direct_fallback: 0,
  },
  devices: [],
}

it('keeps both network sections visible when no Mac is registered yet', async () => {
  render(<NetworkPage loadAgents={async () => emptyAgents} loadCompute={async () => emptyCompute} />)

  expect(screen.getByRole('heading', { name: 'Сеть Mac' })).toBeVisible()
  expect(screen.getByText('Сервер обрабатывает работу первым. Доверенные Mac подключаются как резерв при сбоях и для недоступных серверу ресурсов.')).toBeVisible()
  expect(await screen.findByText('Зарегистрированных Mac пока нет.')).toBeVisible()
  expect(screen.getByText('После входа в Mac-приложение устройство появится здесь автоматически.')).toBeVisible()
})

it('shows both failures instead of turning the network page into an empty screen', async () => {
  render(<NetworkPage loadAgents={async () => { throw new Error('agents down') }} loadCompute={async () => { throw new Error('compute down') }} />)

  expect(await screen.findByText('Не удалось обновить состояние вычислений. Обработка на этом устройстве остаётся доступна.')).toBeVisible()
  expect(screen.getByText('Статус общего пула временно недоступен.')).toBeVisible()
})
