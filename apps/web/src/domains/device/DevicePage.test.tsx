import { act, fireEvent, render, screen } from '@testing-library/react'
import { StrictMode } from 'react'
import userEvent from '@testing-library/user-event'
import { expect, it, vi } from 'vitest'
import { DiagnosticsUnavailableError, type DesktopAgentStatus, type DiagnosticClient } from './deviceClient'
import { DevicePage } from './DevicePage'
import { DiagnosticPanel } from './DiagnosticPanel'

const status: DesktopAgentStatus = {
  version: 1,
  account: { state: 'signed_in', label: 'Test Royal', role: 'royal', registered: true },
  service: { state: 'running' },
  server: { state: 'connected', lastSuccessAt: '2026-10-15T10:00:00Z' },
  sync: null,
  corporate: { state: 'login_required', canLogin: true, ownerLoginConfigured: false, probeConfigured: false, tracker: 'login_required', inventory: 'login_required', emergency: 'unverified', message: null },
  autostart: { supported: true, enabled: true },
  work: { paused: false },
  legacyWorkspaceAvailable: false,
  update: { state: 'current', currentVersion: '0.2.3-rc.5', availableVersion: null, message: null },
}

const diagnostics = (overrides: Partial<DiagnosticClient> = {}): DiagnosticClient => ({
  snapshot: vi.fn(async () => ({ events: [], running: false, results: [] })),
  run: vi.fn(async () => ({ events: [], running: false, results: [] })),
  ...overrides,
})

it('lets an ordinary desktop user start corporate login without entering credentials into the web page', async () => {
  const action = vi.fn(async () => ({
    ...status,
    corporate: { ...status.corporate, state: 'checking' as const },
  }))
  render(<DevicePage client={{ status: async () => status, action }} desktop />)

  expect(await screen.findByText('Test Royal · royal')).toBeVisible()
  expect(screen.getByText('Основную обработку выполняет сервер. Этот Mac подключается как резерв при сбоях и для задач, которым нужны его локальные доступы или ресурсы.')).toBeVisible()
  expect(screen.getByText('Вход в корпоративные системы не выполнен')).toBeVisible()
  expect(screen.queryByLabelText(/пароль/i)).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Войти в корпоративные системы' }))
  expect(action).toHaveBeenCalledWith({ action: 'corporateLogin' })
  expect(screen.getByText('Ожидаем завершения входа…')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Войти в корпоративные системы' })).toBeDisabled()
})

it('explains the connection prerequisite and keeps login disabled while the server is offline', async () => {
  render(<DevicePage client={{ status: async () => ({ ...status, server: { state: 'offline', lastSuccessAt: null } }), action: vi.fn() }} desktop />)

  expect(await screen.findByText('Для входа сначала восстановите связь с сервером.')).toBeVisible()
  expect(screen.getByRole('button', { name: 'Войти в корпоративные системы' })).toBeDisabled()
})

it('offers the isolated legacy window when old WebKit drafts are present', async () => {
  const action = vi.fn(async () => status)
  render(<DevicePage client={{ status: async () => ({ ...status, legacyWorkspaceAvailable: true }), action }} desktop />)

  expect(await screen.findByText('Черновики предыдущей версии')).toBeVisible()
  expect(screen.getByText(/не переносятся в системный браузер автоматически/i)).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: 'Открыть прежнее рабочее окно' }))
  expect(action).toHaveBeenCalledWith({ action: 'openLegacyWorkspace' })
})

it('shows a useful local-service state instead of calling the endpoint in a normal browser', () => {
  const load = vi.fn()
  render(<DevicePage client={{ status: load, action: vi.fn() }} desktop={false} />)

  expect(screen.getByText('Этот раздел доступен в приложении Robopark на Mac.')).toBeVisible()
  expect(load).not.toHaveBeenCalled()
})

it('polls a checking corporate login until it becomes ready without reloading the page', async () => {
  vi.useFakeTimers()
  const checking: DesktopAgentStatus = {
    ...status,
    corporate: { ...status.corporate, state: 'checking' },
  }
  const ready: DesktopAgentStatus = {
    ...status,
    corporate: {
      ...status.corporate,
      state: 'ready', ownerLoginConfigured: true,
      tracker: 'ready', inventory: 'ready', emergency: 'ready',
    },
  }
  const load = vi.fn()
    .mockResolvedValueOnce(checking)
    .mockResolvedValue(ready)
  const view = render(<DevicePage client={{ status: load, action: vi.fn() }} desktop />)

  await act(async () => { await Promise.resolve() })
  expect(screen.getByText('Ожидаем завершения входа…')).toBeVisible()
  await act(async () => { await vi.advanceTimersByTimeAsync(2_500) })
  expect(screen.getByText('Корпоративные доступы готовы.')).toBeVisible()
  expect(load).toHaveBeenCalledTimes(2)

  view.unmount()
  await vi.advanceTimersByTimeAsync(5_000)
  expect(load).toHaveBeenCalledTimes(2)
  vi.useRealTimers()
})

it('does not let an older poll overwrite a completed action', async () => {
  vi.useFakeTimers()
  const checking: DesktopAgentStatus = { ...status, corporate: { ...status.corporate, state: 'checking' } }
  const ready: DesktopAgentStatus = {
    ...status,
    corporate: { ...status.corporate, state: 'ready', tracker: 'ready', inventory: 'ready', emergency: 'ready' },
  }
  let finishPoll: ((value: DesktopAgentStatus) => void) | undefined
  const load = vi.fn()
    .mockResolvedValueOnce(checking)
    .mockImplementationOnce(() => new Promise<DesktopAgentStatus>((resolve) => { finishPoll = resolve }))
  const action = vi.fn(async () => ready)
  render(<DevicePage client={{ status: load, action }} desktop />)
  await act(async () => { await Promise.resolve() })
  await act(async () => { await vi.advanceTimersByTimeAsync(2_500) })

  fireEvent.click(screen.getByRole('button', { name: 'Повторно проверить доступы' }))
  await act(async () => { await Promise.resolve() })
  expect(screen.getByText('Корпоративные доступы готовы.')).toBeVisible()
  await act(async () => { finishPoll?.(checking); await Promise.resolve() })
  expect(screen.getByText('Корпоративные доступы готовы.')).toBeVisible()
  vi.useRealTimers()
})

it('refreshes stable device status when the background service loses connection or installs an update', async () => {
  vi.useFakeTimers()
  const changed: DesktopAgentStatus = {
    ...status, server: { state: 'offline', lastSuccessAt: null },
    update: { ...status.update, message: 'Установлена новая версия. Перезапустите Robopark.' },
  }
  const load = vi.fn().mockResolvedValueOnce(status).mockResolvedValue(changed)
  const view = render(<DevicePage client={{ status: load, action: vi.fn() }} desktop />)
  try {
    await act(async () => { await Promise.resolve() })
    expect(screen.getByText('Сервер на связи')).toBeVisible()
    await act(async () => { await vi.advanceTimersByTimeAsync(15_000) })
    expect(screen.getByText('Сервер недоступен')).toBeVisible()
    expect(screen.getByText(changed.update.message!)).toBeVisible()
    view.unmount()
    await vi.advanceTimersByTimeAsync(30_000)
    expect(load).toHaveBeenCalledTimes(2)
  } finally { view.unmount(); vi.useRealTimers() }
})

it('pauses passive status polling in a hidden tab and refreshes on return', async () => {
  vi.useFakeTimers()
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
  const load = vi.fn().mockResolvedValue(status)
  const view = render(<DevicePage client={{ status: load, action: vi.fn() }} desktop />)
  try {
    await act(async () => { await Promise.resolve() })
    visibility.mockReturnValue('hidden')
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000) })
    expect(load).toHaveBeenCalledTimes(1)
    visibility.mockReturnValue('visible')
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); await Promise.resolve() })
    expect(load).toHaveBeenCalledTimes(2)
  } finally { view.unmount(); visibility.mockRestore(); vi.useRealTimers() }
})

it('hides TEST diagnostics from a non-Royal desktop account', async () => {
  render(<DevicePage client={{ status: async () => ({ ...status, account: { ...status.account, role: 'admin' } }), action: vi.fn() }} desktop diagnostics={diagnostics()} />)

  expect(await screen.findByText('Test Royal · admin')).toBeVisible()
  expect(screen.queryByText('Диагностика TEST')).not.toBeInTheDocument()
})

it('runs a read-only diagnostic with the selected service, route, and robot', async () => {
  const run = vi.fn(async () => ({
    running: false,
    events: [{ id: 'done', time: '12:00:00', service: 'inventory', stage: 'response', status: 'success', elapsedMs: 123 }],
    results: [{ service: 'inventory', route: 'server', robot: '1170', status: 'success', httpStatus: 200, elapsedMs: 123 }],
  }))
  render(<DevicePage client={{ status: async () => status, action: vi.fn() }} desktop diagnostics={diagnostics({ run })} />)

  await userEvent.click(await screen.findByText('Открыть конструктор диагностики'))
  await userEvent.selectOptions(screen.getByLabelText('Сервис'), 'inventory')
  await userEvent.selectOptions(screen.getByLabelText('Маршрут'), 'server')
  await userEvent.click(screen.getByRole('button', { name: 'Запустить проверку' }))

  expect(run).toHaveBeenCalledWith({ service: 'inventory', route: 'server', robot: '1170' })
  expect(await screen.findByText('Inventory · server · робот 1170 · успешно · HTTP 200 · 123 мс')).toBeVisible()
  expect(screen.getByText(/этап response/)).toBeVisible()
})

it('explains that diagnostics require a newer TEST build when the endpoint is absent', async () => {
  render(<DevicePage client={{ status: async () => status, action: vi.fn() }} desktop diagnostics={diagnostics({ snapshot: vi.fn(async () => { throw new DiagnosticsUnavailableError() }) })} />)

  await userEvent.click(await screen.findByText('Открыть конструктор диагностики'))
  expect(await screen.findByText('Диагностика доступна в новой тестовой сборке.')).toBeVisible()
})

it('loads diagnostics after the StrictMode effect replay', async () => {
  const snapshot = vi.fn(async () => ({ running: false, events: [], results: [
    { service: 'server', route: 'direct', status: 'failed', code: 'timeout' },
  ] }))
  render(<StrictMode><DiagnosticPanel client={diagnostics({ snapshot })} /></StrictMode>)
  await userEvent.click(screen.getByText('Открыть конструктор диагностики'))
  expect(await screen.findByText('Сервер TEST · direct · ошибка · timeout')).toBeVisible()
})

it('refreshes a diagnostic that finished while its panel was collapsed', async () => {
  const snapshot = vi.fn()
    .mockResolvedValueOnce({ running: true, events: [], results: [] })
    .mockResolvedValue({ running: false, events: [], results: [
      { service: 'server', route: 'direct', status: 'ok' },
    ] })
  render(<DiagnosticPanel client={diagnostics({ snapshot })} />)
  const toggle = screen.getByText('Открыть конструктор диагностики')
  await userEvent.click(toggle)
  expect(await screen.findByText('Проверка выполняется…')).toBeVisible()
  await userEvent.click(toggle)
  await userEvent.click(toggle)
  expect(await screen.findByText('Сервер TEST · direct · успешно')).toBeVisible()
})
