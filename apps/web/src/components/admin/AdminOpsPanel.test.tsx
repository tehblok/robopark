import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { resourceStore } from '../../lib/resource'
import { AdminOpsPanel } from './AdminOpsPanel'
import { inspectionFixture, jobFixture, mockOpsServer, releaseFixture } from './opsTestFixtures'

const zip = (name = 'release.zip') => new File(['signed-zip'], name, { type: 'application/zip' })
const callsFor = (mock: ReturnType<typeof mockOpsServer>, path: string) => mock.mock.calls.filter(([url]) => String(url).endsWith(path))
beforeEach(() => resourceStore.clearAll())
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('AdminOpsPanel', () => {
  it('shows durable host update progress with the current phase', async () => {
    mockOpsServer({ '/admin/ops/job': {
      ...jobFixture('update'), progress_phase: 'building', progress_percent: 25,
    } })
    render(<AdminOpsPanel />)

    const progress = await screen.findByRole('progressbar', { name: 'Прогресс обновления' })
    expect(progress).toHaveAttribute('value', '25')
    expect(screen.getByText('25%')).toBeVisible()
    expect(screen.getByText('Собираем API и веб-интерфейс')).toBeVisible()
  })

  it('loads idle resources once and describes the diagnostics cookie', async () => {
    const fetchMock = mockOpsServer()
    render(<AdminOpsPanel />)
    await screen.findByText('Сервис Tuna')
    expect(screen.getByText(/cookie диагностики робота придётся ввести заново/i)).toBeVisible()
    for (const path of ['/system-health', '/available-update', '/job']) expect(callsFor(fetchMock, path)).toHaveLength(1)
  })
  it('inspects ZIP before enabling exact typed approval and never installs on selection', async () => {
    const fetchMock = mockOpsServer()
    const user = userEvent.setup()
    render(<AdminOpsPanel />)
    await user.upload(screen.getByLabelText('Архив обновления'), zip())
    expect(await screen.findByText('Версия 1.4.0')).toBeVisible()
    expect(screen.getByText('Улучшена диагностика')).toBeVisible()
    const submit = screen.getByRole('button', { name: 'Установить обновление' })
    expect(submit).toBeDisabled()
    expect(callsFor(fetchMock, '/update/approve')).toHaveLength(0)
    await user.type(screen.getByLabelText('Для установки введите ОБНОВИТЬ'), 'ОБНОВИТЬ ')
    expect(submit).toBeDisabled()
    await user.keyboard('{Backspace}')
    await user.click(submit)
    await waitFor(() => expect(callsFor(fetchMock, '/update/approve')).toHaveLength(1))
    const request = callsFor(fetchMock, '/update/approve')[0] as unknown as [string, RequestInit]
    expect(JSON.parse(String(request[1].body))).toEqual({ inspection_id: 'inspection-one', confirm: 'ОБНОВИТЬ' })
    expect(screen.getByRole('button', { name: 'Прервать и снять техработы' })).toBeVisible()
  })
  it('invalidates old inspection and confirmation when the ZIP changes, ignoring late responses', async () => {
    let resolveFirst!: (value: unknown) => void
    const first = new Promise(resolve => { resolveFirst = resolve })
    let requests = 0
    mockOpsServer({ '/admin/ops/update/inspect': () => ++requests === 1 ? first : { ...inspectionFixture, inspection_id: 'second', version: '2.0.0' } })
    const user = userEvent.setup()
    render(<AdminOpsPanel />)
    await user.upload(screen.getByLabelText('Архив обновления'), zip('first.zip'))
    await user.upload(screen.getByLabelText('Архив обновления'), zip('second.zip'))
    expect(await screen.findByText('Версия 2.0.0')).toBeVisible()
    await act(async () => resolveFirst(inspectionFixture))
    expect(screen.queryByText('Версия 1.4.0')).not.toBeInTheDocument()
    await user.type(screen.getByLabelText('Для установки введите ОБНОВИТЬ'), 'ОБНОВИТЬ')
    await user.upload(screen.getByLabelText('Архив обновления'), zip('third.zip'))
    expect(screen.getByLabelText('Для установки введите ОБНОВИТЬ')).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Установить обновление' })).toBeDisabled()
  })
  it('requires GitHub confirmation dialog, restores focus, and sends only host release id', async () => {
    const fetchMock = mockOpsServer()
    const user = userEvent.setup()
    render(<AdminOpsPanel />)
    const trigger = await screen.findByRole('button', { name: 'Обновить из GitHub' })
    await user.click(trigger)
    const dialog = screen.getByRole('dialog', { name: 'Подтвердить обновление' })
    const input = within(dialog).getByLabelText('Для GitHub введите ОБНОВИТЬ')
    expect(input).toHaveFocus()
    const submit = within(dialog).getByRole('button', { name: 'Установить версию 1.3.0' })
    expect(submit).toBeDisabled()
    await user.keyboard('{Escape}')
    expect(trigger).toHaveFocus()
    await user.click(trigger)
    await user.type(screen.getByLabelText('Для GitHub введите ОБНОВИТЬ'), 'ОБНОВИТЬ')
    await user.click(screen.getByRole('button', { name: 'Установить версию 1.3.0' }))
    await waitFor(() => expect(callsFor(fetchMock, '/github-update/approve')).toHaveLength(1))
    const request = callsFor(fetchMock, '/github-update/approve')[0] as unknown as [string, RequestInit]
    expect(JSON.parse(String(request[1].body))).toEqual({ release_id: 42, confirm: 'ОБНОВИТЬ' })
  })
  it('refreshes stale discovery after approval rejection and blocks another approval', async () => {
    let discovery = 0
    mockOpsServer({ '/admin/ops/available-update': () => ++discovery === 1 ? releaseFixture : { ...releaseFixture, state: 'discovery_stale', release: null }, '/admin/ops/github-update/approve': new Response(JSON.stringify({ detail: 'github_release_unavailable' }), { status: 400, headers: { 'Content-Type': 'application/json' } }) })
    const user = userEvent.setup()
    render(<AdminOpsPanel />)
    await user.click(await screen.findByRole('button', { name: 'Обновить из GitHub' }))
    await user.type(screen.getByLabelText('Для GitHub введите ОБНОВИТЬ'), 'ОБНОВИТЬ')
    await user.click(screen.getByRole('button', { name: 'Установить версию 1.3.0' }))
    expect(await screen.findByText(/Сведения об обновлении устарели/)).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Обновить из GitHub' })).not.toBeInTheDocument()
  })
  it('repairs only on explicit action and presents performed and failed outcomes', async () => {
    const fetchMock = mockOpsServer()
    const user = userEvent.setup()
    render(<AdminOpsPanel />)
    const repair = await screen.findByRole('button', { name: 'Исправить безопасные проблемы' })
    expect(callsFor(fetchMock, '/repair')).toHaveLength(0)
    await user.click(repair)
    expect(await screen.findByText('Выполнено: перезапуск Tuna')).toBeVisible()
    expect(screen.getByText('Не выполнено: перезапуск приложения')).toBeVisible()
  })
  it('allows diagnostic download for a completed typed diagnostic job', async () => {
    const user = userEvent.setup()
    mockOpsServer({ '/admin/ops/job': jobFixture('diagnostics', 'succeeded'), '/admin/ops/diagnostic-artifact': new Response('diagnostic zip') })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => 'blob:diagnostic'), revokeObjectURL: vi.fn() }))
    render(<AdminOpsPanel />)
    const download = screen.getByRole('button', { name: 'Скачать диагностику' })
    await screen.findByText('Завершено')
    expect(download).toBeEnabled()
    await user.click(download)
    await waitFor(() => expect(click).toHaveBeenCalledOnce())
    expect(screen.getByRole('button', { name: 'Скачать архив' })).toBeDisabled()
  })
  it('pauses active polling while hidden and stops on completion and unmount', async () => {
    vi.useFakeTimers()
    let count = 0
    const fetchMock = mockOpsServer({ '/admin/ops/job': () => jobFixture('update', ++count >= 3 ? 'succeeded' : 'running') })
    const { unmount } = render(<AdminOpsPanel />)
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    const before = callsFor(fetchMock, '/job').length
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    fireEvent(document, new Event('visibilitychange'))
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(callsFor(fetchMock, '/job')).toHaveLength(before)
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
    fireEvent(document, new Event('visibilitychange'))
    await act(async () => { await vi.advanceTimersByTimeAsync(15000) })
    expect(screen.getByText('Завершено')).toBeVisible()
    const done = callsFor(fetchMock, '/job').length
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(callsFor(fetchMock, '/job')).toHaveLength(done)
    unmount()
    await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
    expect(callsFor(fetchMock, '/job')).toHaveLength(done)
  })
})

it('does not refetch idle resources twice when mounting a previously completed job', async () => {
  const fetchMock = mockOpsServer({ '/admin/ops/job': jobFixture('repair', 'succeeded') })
  render(<AdminOpsPanel />)
  await screen.findByText('Завершено')
  await act(async () => undefined)
  expect(callsFor(fetchMock, '/system-health')).toHaveLength(1)
  expect(callsFor(fetchMock, '/available-update')).toHaveLength(1)
})

it('backs off after a transient polling failure while keeping the active operation visible', async () => {
  vi.useFakeTimers()
  let count = 0
  const fetchMock = mockOpsServer({ '/admin/ops/job': () => { if (++count > 1) throw new Error('offline'); return jobFixture('update') } })
  render(<AdminOpsPanel />)
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  expect(callsFor(fetchMock, '/job')).toHaveLength(1)
  await act(async () => { await vi.advanceTimersByTimeAsync(2500) })
  expect(callsFor(fetchMock, '/job')).toHaveLength(2)
  expect(screen.getByText('Выполняется')).toBeVisible()
  await act(async () => { await vi.advanceTimersByTimeAsync(4999) })
  expect(callsFor(fetchMock, '/job')).toHaveLength(2)
  await act(async () => { await vi.advanceTimersByTimeAsync(1) })
  expect(callsFor(fetchMock, '/job')).toHaveLength(3)
})

it.each([
  ['up_to_date', 'Установлена актуальная версия'],
  ['disabled', 'Проверка обновлений отключена на хосте'],
  ['approved', 'Обновление уже подтверждено'],
  ['discovery_stale', 'Сведения об обновлении устарели. Ожидаем проверку хоста.'],
])('does not offer approval when discovery is %s', async (state, label) => {
  mockOpsServer({ '/admin/ops/available-update': { ...releaseFixture, state } })
  render(<AdminOpsPanel />)
  expect(await screen.findByText(label)).toBeVisible()
  expect(screen.queryByRole('button', { name: 'Обновить из GitHub' })).not.toBeInTheDocument()
})

it.each([1, 2])('keeps the accepted job after %i dispatches isolated from a pre-dispatch GET across hidden/resume', async (dispatchCount) => {
  vi.useFakeTimers()
  let resolveBeforeDispatch!: (value: unknown) => void
  let resolveAfterDispatch!: (value: unknown) => void
  const beforeDispatch = new Promise(resolve => { resolveBeforeDispatch = resolve })
  const afterDispatch = new Promise(resolve => { resolveAfterDispatch = resolve })
  let reads = 0
  let dispatches = 0
  const currentJob = { ...jobFixture('repair'), id: `job-${dispatchCount}` }
  const fetchMock = mockOpsServer({
    '/admin/ops/job': () => ++reads === 1 ? beforeDispatch : reads === 2 ? afterDispatch : { ...currentJob, state: 'succeeded', artifact_ready: true },
    '/admin/ops/repair': () => ({ ...jobFixture('repair', ++dispatches === dispatchCount ? 'running' : 'succeeded'), id: `job-${dispatches}` }),
  })
  render(<AdminOpsPanel />)
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  expect(callsFor(fetchMock, '/job')).toHaveLength(1)
  const start = screen.getByRole('button', { name: 'Исправить безопасные проблемы' })
  for (let dispatch = 0; dispatch < dispatchCount; dispatch++) {
    await act(async () => { fireEvent.click(start) })
  }
  expect(screen.getByText('Выполняется')).toBeVisible()
  expect(start).toBeDisabled()

  // A timer now tries to read the accepted operation while the original idle
  // GET is still in flight. Its old snapshot must never become the current job.
  await act(async () => { await vi.advanceTimersByTimeAsync(2500) })
  await act(async () => { resolveBeforeDispatch(jobFixture('', 'idle')) })
  expect(screen.getByText('Выполняется')).toBeVisible()
  expect(start).toBeDisabled()
  expect(callsFor(fetchMock, '/job')).toHaveLength(2)

  const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
  fireEvent(document, new Event('visibilitychange'))
  await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
  expect(callsFor(fetchMock, '/job')).toHaveLength(2)
  hidden.mockReturnValue(false)
  await act(async () => {
    fireEvent(document, new Event('visibilitychange'))
    fireEvent(window, new Event('focus'))
    fireEvent(window, new Event('focus'))
  })
  expect(callsFor(fetchMock, '/job')).toHaveLength(2)
  await act(async () => { resolveAfterDispatch(currentJob) })
  expect(start).toBeDisabled()
  await act(async () => { await vi.advanceTimersByTimeAsync(2500) })
  expect(callsFor(fetchMock, '/job')).toHaveLength(3)
  expect(screen.getByText('Завершено')).toBeVisible()
  expect(start).toBeEnabled()
  await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
  expect(callsFor(fetchMock, '/job')).toHaveLength(3)
})
