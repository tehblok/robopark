import { act, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { resourceStore } from '../../lib/resource'
import { AdminOpsPanel } from './AdminOpsPanel'
import { jobFixture, mockOpsServer } from './opsTestFixtures'

const callsFor = (mock: ReturnType<typeof mockOpsServer>, path: string) => mock.mock.calls.filter(([url]) => String(url).endsWith(path))

beforeEach(() => resourceStore.clearAll())
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
const renderPanel = (route = '/admin/settings?tab=ops') => render(<MemoryRouter initialEntries={[route]}><AdminOpsPanel /></MemoryRouter>)

it('is read-only and exposes no legacy host mutation controls', async () => {
  const fetchMock = mockOpsServer()
  renderPanel()
  await screen.findByText('Сервис Tuna')

  for (const name of [
    'Исправить безопасные проблемы', 'Обновить из GitHub', 'Установить обновление',
    'Создать снимок', 'Восстановить', 'Прервать и снять техработы',
  ]) expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Архив обновления')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Архив восстановления')).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.every(call => {
    const init = (call as unknown as [unknown, RequestInit?])[1]
    return !init?.method || init.method === 'GET'
  })).toBe(true)
})

it('shows durable host progress without exposing abort', async () => {
  mockOpsServer({ '/admin/ops/job': {
    ...jobFixture('update'), progress_phase: 'building', progress_percent: 25,
  } })
  renderPanel()

  const progress = await screen.findByRole('progressbar', { name: 'Прогресс обновления' })
  expect(progress).toHaveAttribute('value', '25')
  expect(screen.getByText('Собираем API и веб-интерфейс')).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Прервать и снять техработы' })).not.toBeInTheDocument()
})

it('loads only live read-only host resources once', async () => {
  const fetchMock = mockOpsServer()
  renderPanel()
  await screen.findByText('Сервис Tuna')
  expect(screen.getByText('Версия системы')).toBeVisible()
  for (const path of ['/system-health', '/release-status', '/job']) {
    expect(callsFor(fetchMock, path)).toHaveLength(1)
  }
  expect(callsFor(fetchMock, '/available-update')).toHaveLength(0)
  expect(callsFor(fetchMock, '/diagnostic-artifact')).toHaveLength(0)
})

it('links diagnostics and updates to the live System page with the selected park', async () => {
  const fetchMock = mockOpsServer({ '/admin/ops/job': jobFixture('diagnostics', 'succeeded') })
  renderPanel('/admin/settings?park=7&tab=ops')

  await screen.findByText('Завершено')
  expect(screen.getByRole('link', { name: 'Открыть системные операции' })).toHaveAttribute('href', '/system?park=7#system-operations')
  expect(screen.queryByRole('button', { name: 'Скачать диагностику' })).not.toBeInTheDocument()
  expect(callsFor(fetchMock, '/available-update')).toHaveLength(0)
  expect(callsFor(fetchMock, '/diagnostic-artifact')).toHaveLength(0)
})

it('keeps polling an active operation until completion', async () => {
  vi.useFakeTimers()
  let count = 0
  const fetchMock = mockOpsServer({
    '/admin/ops/job': () => jobFixture('update', ++count >= 3 ? 'succeeded' : 'running'),
  })
  renderPanel()
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  await act(async () => { await vi.advanceTimersByTimeAsync(15000) })
  expect(screen.getByText('Завершено')).toBeVisible()
  const done = callsFor(fetchMock, '/job').length
  await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
  expect(callsFor(fetchMock, '/job')).toHaveLength(done)
})
