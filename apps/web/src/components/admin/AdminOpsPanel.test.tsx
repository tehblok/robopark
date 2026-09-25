import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { resourceStore } from '../../lib/resource'
import { AdminOpsPanel } from './AdminOpsPanel'
import { jobFixture, mockOpsServer, releaseFixture } from './opsTestFixtures'

const callsFor = (mock: ReturnType<typeof mockOpsServer>, path: string) => mock.mock.calls.filter(([url]) => String(url).endsWith(path))

beforeEach(() => resourceStore.clearAll())
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('is read-only and exposes no legacy host mutation controls', async () => {
  const fetchMock = mockOpsServer()
  render(<AdminOpsPanel />)
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
  render(<AdminOpsPanel />)

  const progress = await screen.findByRole('progressbar', { name: 'Прогресс обновления' })
  expect(progress).toHaveAttribute('value', '25')
  expect(screen.getByText('Собираем API и веб-интерфейс')).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Прервать и снять техработы' })).not.toBeInTheDocument()
})

it('loads read-only host resources once', async () => {
  const fetchMock = mockOpsServer()
  render(<AdminOpsPanel />)
  await screen.findByText('Сервис Tuna')
  expect(screen.getByText('Версия системы')).toBeVisible()
  for (const path of ['/system-health', '/available-update', '/job']) {
    expect(callsFor(fetchMock, path)).toHaveLength(1)
  }
})

it('allows download of an already completed typed diagnostic artifact', async () => {
  const user = userEvent.setup()
  mockOpsServer({
    '/admin/ops/job': jobFixture('diagnostics', 'succeeded'),
    '/admin/ops/diagnostic-artifact': new Response('diagnostic zip'),
  })
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
  vi.stubGlobal('URL', Object.assign(URL, {
    createObjectURL: vi.fn(() => 'blob:diagnostic'), revokeObjectURL: vi.fn(),
  }))
  render(<AdminOpsPanel />)
  const download = screen.getByRole('button', { name: 'Скачать диагностику' })
  await screen.findByText('Завершено')
  expect(download).toBeEnabled()
  await user.click(download)
  await waitFor(() => expect(click).toHaveBeenCalledOnce())
})

it.each([
  ['available', 'Доступна новая версия'],
  ['up_to_date', 'Установлена актуальная версия'],
  ['disabled', 'Проверка обновлений отключена на хосте'],
  ['approved', 'Обновление уже подтверждено'],
  ['discovery_stale', 'Сведения об обновлении устарели. Ожидаем проверку хоста.'],
])('shows %s discovery as information only', async (state, label) => {
  mockOpsServer({ '/admin/ops/available-update': { ...releaseFixture, state } })
  render(<AdminOpsPanel />)
  expect(await screen.findByText(label)).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Обновить из GitHub' })).not.toBeInTheDocument()
})

it('keeps polling an active operation until completion', async () => {
  vi.useFakeTimers()
  let count = 0
  const fetchMock = mockOpsServer({
    '/admin/ops/job': () => jobFixture('update', ++count >= 3 ? 'succeeded' : 'running'),
  })
  render(<AdminOpsPanel />)
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  await act(async () => { await vi.advanceTimersByTimeAsync(15000) })
  expect(screen.getByText('Завершено')).toBeVisible()
  const done = callsFor(fetchMock, '/job').length
  await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
  expect(callsFor(fetchMock, '/job')).toHaveLength(done)
})
