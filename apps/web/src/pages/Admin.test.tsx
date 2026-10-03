import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, type IntegrationSettings, type Park } from '../api'
import { resourceStore } from '../lib/resource'
import { snapshot } from '../domains/insights/operations.test-support'
import { installMatchMedia, renderApp, testUser } from '../test/renderApp'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

function settings(validation: Partial<IntegrationSettings> = {}): IntegrationSettings {
  return {
    tracker_token_masked: null,
    tracker_token_updated_at: null,
    emergency_cookie_masked: null,
    emergency_cookie_updated_at: null,
    emergency_cookie_valid: null,
    emergency_cookie_status: 'unchecked',
    emergency_cookie_checked_at: null,
    emergency_cookie_checked_robot: null,
    ...validation,
  }
}

function setup(validation: Partial<IntegrationSettings> = {}, parks: Park[] = [], permissions = ['nav.admin'], route = '/admin/settings', role: 'admin' | 'royal' = 'admin', configure?: () => void) {
  vi.spyOn(api, 'parks').mockResolvedValue(parks)
  vi.spyOn(api, 'adminParkRequests').mockResolvedValue([])
  vi.spyOn(api, 'integrationSettings').mockResolvedValue(settings(validation))
  vi.spyOn(api, 'trackerPolicy').mockResolvedValue({
    operator_show_untagged: false,
    operator_show_raw: false,
    operator_show_firmware_profile: false,
    mechanic_can_write: false,
  })
  vi.spyOn(api, 'screenshotGuardSettings').mockResolvedValue({
    operator: false, mechanic: false, admin: false, royal: false, driver: false,
  })
  vi.spyOn(api, 'adminUsers').mockResolvedValue([])
  configure?.()
  return renderApp(route, testUser({ role, permissions, parks }))
}

describe('Admin Emergency cookie validation', () => {
  beforeEach(() => {
    installMatchMedia()
  })

  afterEach(() => {
    cleanup()
    resourceStore.clearAll()
    vi.restoreAllMocks()
  })

  it('describes a configured registration password without showing its masked API value', async () => {
    vi.spyOn(api, 'registrationPasswordSettings').mockResolvedValue({ configured: true, password_masked: 'set', updated_at: null })
    setup({}, [], ['nav.admin'], '/admin/settings', 'royal')

    const status = await screen.findByText('Текущий пароль')
    expect(status.parentElement).toHaveTextContent('Установлен')
    expect(status.parentElement).not.toHaveTextContent('set')
  })

  it('offers collapse controls for substantial administration panels but not navigation links', async () => {
    setup()

    expect(await screen.findByRole('button', { name: 'Свернуть: Секреты' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Свернуть: Политика Tracker' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Свернуть: Быстрые переходы' })).not.toBeInTheDocument()
  })

  it('does not fetch the pending user list while opening integration settings', async () => {
    setup()

    expect(await screen.findByRole('button', { name: 'Свернуть: Секреты' })).toBeVisible()
    expect(api.adminUsers).not.toHaveBeenCalled()
  })

  it('keeps available integration settings when Tracker policy fails temporarily', async () => {
    setup({ tracker_token_masked: 'configured' }, [], ['nav.admin'], '/admin/settings', 'admin', () => {
      vi.mocked(api.trackerPolicy).mockRejectedValue(new ApiError(503, 'upstream_unavailable'))
    })

    expect(await screen.findByText('Tracker OAuth')).toBeVisible()
    expect(screen.getByText('работает')).toBeVisible()
    expect(await screen.findByText(/Не удалось загрузить: Политика Tracker/)).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Политика Tracker' })).not.toBeInTheDocument()
    expect(await screen.findByRole('checkbox', { name: 'Запрет скриншотов — Оператор' })).toBeEnabled()
  })

  it('keeps park settings when park requests fail temporarily', async () => {
    setup({}, [{ id: 7, name: 'Северный', timezone: 'Europe/Moscow', tag: 'north', is_active: true }], ['nav.admin', 'parks.manage'], '/admin/settings?tab=parks', 'admin', () => {
      vi.mocked(api.adminParkRequests).mockRejectedValue(new ApiError(503, 'upstream_unavailable'))
    })

    expect(await screen.findByRole('button', { name: 'Открыть парк Северный' })).toBeVisible()
    expect(screen.getByText(/Не удалось загрузить: Заявки на парки/)).toBeVisible()
  })

  it('loads only park data on the parks route and fetches integrations when settings opens', async () => {
    const actor = userEvent.setup()
    setup({}, [{ id: 7, name: 'Северный', timezone: 'Europe/Moscow', tag: 'north', is_active: true }], ['nav.admin', 'parks.manage'], '/admin/settings?tab=parks')
    vi.mocked(api.integrationSettings).mockRejectedValue(new ApiError(503, 'upstream_unavailable'))
    vi.mocked(api.trackerPolicy).mockRejectedValue(new ApiError(503, 'upstream_unavailable'))

    expect(await screen.findByRole('button', { name: 'Открыть парк Северный' })).toBeVisible()
    expect(api.adminParkRequests).toHaveBeenCalled()
    expect(api.integrationSettings).not.toHaveBeenCalled()
    expect(api.trackerPolicy).not.toHaveBeenCalled()
    expect(screen.queryByText(/Не удалось загрузить: Интеграции/)).not.toBeInTheDocument()

    await actor.click(screen.getByRole('link', { name: /^Настройки$/ }))
    await waitFor(() => expect(api.integrationSettings).toHaveBeenCalledTimes(1))
    expect(await screen.findByText(/Не удалось загрузить: Интеграции/)).toBeVisible()
  })

  it('keeps the last confirmed integration status when its revalidation fails', async () => {
    const first = setup({ tracker_token_masked: 'configured' })
    await screen.findByText('работает')
    first.unmount()
    vi.mocked(api.integrationSettings).mockRejectedValue(new ApiError(503, 'upstream_unavailable'))
    vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 30_001)

    renderApp('/admin/settings', testUser({ role: 'admin', permissions: ['nav.admin'] }))
    await screen.findByText(/Не удалось загрузить: Интеграции/)
    expect(screen.getByText('работает')).toBeVisible()
  })

  it('drops protected settings when one bootstrap request is denied', async () => {
    setup({ tracker_token_masked: 'configured' }, [], ['nav.admin'], '/admin/settings', 'admin', () => {
      vi.mocked(api.trackerPolicy).mockRejectedValue(new ApiError(403, 'forbidden'))
    })

    expect(await screen.findByRole('alert')).toHaveTextContent('Недостаточно прав')
    expect(screen.queryByText('работает')).not.toBeInTheDocument()
  })

  it('drops a cached integration status after revalidation is denied', async () => {
    const first = setup({ tracker_token_masked: 'configured' })
    await screen.findByText('работает')
    first.unmount()
    vi.mocked(api.trackerPolicy).mockRejectedValue(new ApiError(403, 'forbidden'))
    vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 30_001)

    renderApp('/admin/settings', testUser({ role: 'admin', permissions: ['nav.admin'] }))
    await screen.findByRole('alert')
    expect(screen.queryByText('работает')).not.toBeInTheDocument()
  })

  it('links directly to error mapping and ignoring from administration', async () => {
    setup()
    expect(await screen.findByRole('link', { name: 'Разметка и игнорирование ошибок' }))
      .toHaveAttribute('href', '/admin/emergency/config?tab=errors')
  })

  it('never includes integration secret names or values in collapsed summaries', async () => {
    setup()
    await screen.findByRole('button', { name: 'Свернуть: Секреты' })
    fireEvent.click(screen.getByRole('button', { name: 'Свернуть: Секреты' }))
    const summary = screen.getByRole('button', { name: 'Развернуть: Секреты' }).closest('.panel-head')
    expect(summary).not.toHaveTextContent(/session_id|tracker_token|cookie|oauth-токен/i)
    fireEvent.click(screen.getByRole('button', { name: 'Развернуть: Секреты' }))
  })

  it('keeps the new-park form behind an explicit disclosure', async () => {
    setup({}, [{ id: 7, name: 'Северный', timezone: 'Europe/Moscow', tag: 'north', is_active: true }], ['nav.admin', 'parks.manage'], '/admin/settings?park=7&tab=parks')
    const create = await screen.findByRole('button', { name: 'Добавить парк' })
    expect(screen.queryByRole('heading', { name: 'Новый парк' })).not.toBeInTheDocument()
    fireEvent.click(create)
    expect(screen.getByRole('heading', { name: 'Новый парк' })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Открыть парк Северный' }))
    expect(screen.queryByRole('heading', { name: 'Новый парк' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Очередь Tracker')).toBeVisible()
    expect(screen.getByText(/SLA — 5 рабочих часов/)).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Настроить SLA' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Добавить парк' }))
    expect(screen.getByRole('heading', { name: 'Новый парк' })).toBeVisible()
  })

  it('shows parks in their own management section rather than a duplicate settings tab', async () => {
    setup({}, [{ id: 7, name: 'Северный', timezone: 'Europe/Moscow', tag: 'north', is_active: true }], ['nav.admin', 'parks.manage'], '/admin/settings?tab=parks')
    await waitFor(() => expect(within(screen.getByRole('region', { name: 'Парки' })).getByRole('button', { name: 'Открыть парк Северный' })).toBeVisible())
    expect(screen.queryByRole('tablist', { name: 'Разделы настроек' })).not.toBeInTheDocument()
  })

  it('configures the Tracker queue while creating the first park', async () => {
    setup({}, [], ['nav.admin', 'parks.manage'], '/admin/settings?tab=parks')
    const create = vi.spyOn(api, 'createPark').mockResolvedValue({
      id: 7, name: 'Северный', tag: 'north', timezone: 'Europe/Moscow',
      tracker_queue: 'SDCFLEETOPS', is_active: true,
    })
    fireEvent.click(await screen.findByRole('button', { name: 'Добавить парк' }))
    fireEvent.change(screen.getByLabelText('Название'), { target: { value: 'Северный' } })
    fireEvent.change(screen.getByLabelText('Тег'), { target: { value: 'north' } })
    fireEvent.change(screen.getByLabelText(/Очередь Tracker/), { target: { value: 'SDCFLEETOPS' } })
    fireEvent.click(screen.getByRole('button', { name: 'Создать' }))
    await waitFor(() => expect(create).toHaveBeenCalledWith(expect.objectContaining({
      name: 'Северный', tag: 'north', timezone: 'Europe/Moscow', tracker_queue: 'SDCFLEETOPS',
    })))
  })

  it('closes the new-park form after a successful creation', async () => {
    const existing: Park = { id: 7, name: 'Северный', tag: 'north', timezone: 'Europe/Moscow', is_active: true }
    const created: Park = { id: 8, name: 'Тестовый парк', tag: 'demo-third', timezone: 'Europe/Moscow', is_active: true }
    let available = [existing]
    setup({}, available, ['nav.admin', 'parks.manage'], '/admin/settings?tab=parks')
    vi.mocked(api.parks).mockImplementation(async () => available)
    vi.spyOn(api, 'createPark').mockImplementation(async () => {
      available = [existing, created]
      return created
    })

    await screen.findByRole('button', { name: 'Открыть парк Северный' })
    fireEvent.click(screen.getByRole('button', { name: 'Добавить парк' }))
    fireEvent.change(screen.getByLabelText('Название'), { target: { value: created.name } })
    fireEvent.change(screen.getByLabelText('Тег'), { target: { value: created.tag } })
    fireEvent.click(screen.getByRole('button', { name: 'Создать' }))

    await screen.findByText('Парк создан')
    expect(await screen.findByRole('button', { name: 'Открыть парк Тестовый парк' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Новый парк' })).not.toBeInTheDocument()
  })

  it('opens the new park in the overview immediately after the first creation', async () => {
    const created: Park = { id: 7, name: 'Северный', tag: 'north', timezone: 'Europe/Moscow', is_active: true }
    let available: Park[] = []
    setup({}, [], ['nav.admin', 'parks.manage', 'nav.dashboard', 'tracker.read'], '/admin/settings?tab=parks')
    vi.mocked(api.parks).mockImplementation(async () => available)
    vi.spyOn(api, 'createPark').mockImplementation(async () => {
      available = [created]
      return created
    })
    const overview = vi.spyOn(api, 'operationsOverview').mockResolvedValue(snapshot())

    fireEvent.click(await screen.findByRole('button', { name: 'Добавить парк' }))
    fireEvent.change(screen.getByLabelText('Название'), { target: { value: 'Северный' } })
    fireEvent.change(screen.getByLabelText('Тег'), { target: { value: 'north' } })
    fireEvent.click(screen.getByRole('button', { name: 'Создать' }))
    await screen.findByText('Парк создан')
    fireEvent.click(screen.getAllByRole('link', { name: 'Обзор' })[0])

    await waitFor(() => expect(overview).toHaveBeenCalledWith(7, 7, 'all'))
  })

  it('uses the updated park name across the shell and overview without reloading', async () => {
    const north: Park = { id: 7, name: 'Северный', tag: 'north', timezone: 'Europe/Moscow', is_active: true }
    let available = [north]
    setup({}, [north], ['nav.admin', 'parks.manage', 'nav.dashboard', 'tracker.read'], '/admin/settings?tab=parks')
    vi.mocked(api.parks).mockImplementation(async () => available)
    vi.spyOn(api, 'updatePark').mockImplementation(async () => {
      const updated = { ...north, name: 'Южный' }
      available = [updated]
      return updated
    })
    vi.spyOn(api, 'operationsOverview').mockResolvedValue(snapshot())

    fireEvent.click(await screen.findByRole('button', { name: 'Открыть парк Северный' }))
    fireEvent.change(screen.getByLabelText('Название'), { target: { value: 'Южный' } })
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }))
    await screen.findByRole('button', { name: 'Открыть парк Южный' })
    fireEvent.click(screen.getAllByRole('link', { name: 'Обзор' })[0])

    expect(await screen.findByRole('region', { name: 'Южный' })).toBeVisible()
  })

  it('blocks an invalid park time zone before creating or updating SLA settings', async () => {
    const park: Park = { id: 7, name: 'Северный', tag: 'north', timezone: 'Europe/Moscow', is_active: true }
    setup({}, [park], ['nav.admin', 'parks.manage'], '/admin/settings?park=7&tab=parks')
    const create = vi.spyOn(api, 'createPark').mockResolvedValue(park)
    const update = vi.spyOn(api, 'updatePark').mockResolvedValue(park)
    fireEvent.click(await screen.findByRole('button', { name: 'Добавить парк' }))
    fireEvent.change(screen.getByLabelText(/Часовой пояс парка/), { target: { value: 'Mars/Olympus' } })
    expect(screen.getByRole('button', { name: 'Создать' })).toBeDisabled()
    expect(screen.getByText(/Укажите действительный часовой пояс IANA/)).toBeVisible()
    expect(create).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Открыть парк Северный' }))
    fireEvent.change(screen.getByLabelText(/Часовой пояс парка/), { target: { value: 'Mars/Olympus' } })
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled()
    expect(update).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText(/Часовой пояс парка/), { target: { value: 'Asia/Yekaterinburg' } })
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeEnabled()
  })

  it('shows the park search and identities before opening any park editor at 390px', async () => {
    installMatchMedia({ width: 390 })
    setup({}, [{ id: 7, name: 'Северный', timezone: 'Europe/Moscow', tag: 'north', is_active: true }], ['nav.admin', 'parks.manage'], '/admin/settings?park=7&tab=parks')
    await screen.findByRole('searchbox', { name: 'Поиск парков' })
    expect(screen.getByRole('searchbox', { name: 'Поиск парков' })).toBeVisible()
    expect(await screen.findByRole('button', { name: 'Открыть парк Северный' })).toBeVisible()
    expect(screen.queryByLabelText('Очередь Tracker')).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Новый парк' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Открыть парк Северный' }))
    expect(screen.getByLabelText('Очередь Tracker')).toBeVisible()
  })

  it('does not expose removed park coordinate controls', async () => {
    const park: Park = { id: 7, name: 'Северный', tag: 'north', timezone: 'Europe/Moscow', is_active: true }
    setup({}, [park], ['nav.admin', 'parks.manage'], '/admin/settings?park=7&tab=parks')
    fireEvent.click(await screen.findByRole('button', { name: 'Открыть парк Северный' }))
    expect(screen.queryByLabelText('Широта парка')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Долгота парка')).not.toBeInTheDocument()
  })

  it('keeps an unsaved park draft when stale data could otherwise reload on focus', async () => {
    const parks = [{ id: 7, name: 'Северный', timezone: 'Europe/Moscow', tag: 'north', is_active: true }]
    setup({}, parks, ['nav.admin', 'parks.manage'], '/admin/settings?park=7&tab=parks')
    fireEvent.click(await screen.findByRole('button', { name: 'Открыть парк Северный' }))
    const input = await screen.findByDisplayValue('Северный')
    fireEvent.change(input, { target: { value: 'Название в работе' } })
    const calls = vi.mocked(api.adminParkRequests).mock.calls.length
    vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 120_001)
    fireEvent.focus(window)
    fireEvent(window, new Event('online'))
    await act(async () => {})
    expect(input).toHaveValue('Название в работе')
    expect(api.adminParkRequests).toHaveBeenCalledTimes(calls)
  })

  it('renders an invalid cookie status without exposing the secret', async () => {
    setup({
      emergency_cookie_status: 'invalid',
      emergency_cookie_valid: false,
      emergency_cookie_checked_robot: '447',
      emergency_cookie_checked_at: '2026-09-06T09:00:00Z',
    })

    expect(await screen.findByText('Недействительна')).toBeVisible()
    expect(screen.getByText(/робот 447/i)).toBeVisible()
    expect(document.body).not.toHaveTextContent('secret-cookie')
  })

  it('shows an unavailable cookie warning with a retry action', async () => {
    setup({ emergency_cookie_status: 'unavailable', emergency_cookie_valid: null })

    expect(await screen.findByText(/Проверка сейчас недоступна/)).toBeVisible()
    expect(screen.getByRole('button', { name: 'Проверить текущую' })).toBeEnabled()
  })

  it.each([
    [401, 'emergency_cookie_invalid', 'invalid', 'Недействительна'],
    [503, 'emergency_upstream_unavailable', 'unavailable', 'Недоступна'],
  ] as const)('applies authoritative metadata after a %s saved-cookie recheck', async (code, detail, status, label) => {
    const actor = userEvent.setup()
    vi.spyOn(api, 'checkEmergencyCookie').mockRejectedValue(new ApiError(code, detail))
    setup({ emergency_cookie_status: 'valid', emergency_cookie_valid: true })
    await screen.findByText('Действительна')
    vi.mocked(api.integrationSettings).mockResolvedValue(settings({
      emergency_cookie_status: status,
      emergency_cookie_valid: code === 401 ? false : true,
      emergency_cookie_checked_robot: '448',
    }))
    await actor.type(screen.getByLabelText('Cookie диагностики робота'), 'candidate-fixture')
    await actor.click(screen.getByRole('button', { name: 'Проверить текущую' }))

    expect(await screen.findByText(label)).toBeVisible()
    expect(screen.queryByText('Действительна')).not.toBeInTheDocument()
    expect(screen.getByText(/робот 448/)).toBeVisible()
    if (code === 503) expect(screen.getByText(/Проверка сейчас недоступна/)).toBeVisible()
    expect(screen.getByLabelText('Cookie диагностики робота')).toHaveValue('candidate-fixture')
  })

  it('keeps a completed mutation in the UI and cache after a late bootstrap', async () => {
    const actor = userEvent.setup()
    const first = setup()
    await screen.findByText('Не проверена')
    first.unmount()
    const late = deferred<IntegrationSettings>()
    vi.mocked(api.integrationSettings).mockReturnValue(late.promise)
    vi.spyOn(api, 'checkEmergencyCookie').mockResolvedValue(settings({
      emergency_cookie_status: 'valid', emergency_cookie_valid: true,
    }))
    vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 30_001)
    const second = renderApp('/admin/settings', testUser({ role: 'admin', permissions: ['nav.admin'] }))
    await waitFor(() => expect(api.integrationSettings).toHaveBeenCalledTimes(2))
    await actor.click(screen.getByRole('button', { name: 'Проверить текущую' }))
    await screen.findByText('Действительна')
    await act(async () => late.resolve(settings()))
    expect(screen.getByText('Действительна')).toBeVisible()

    second.unmount()
    vi.mocked(api.integrationSettings).mockReturnValue(new Promise(() => {}))
    renderApp('/admin/settings', testUser({ role: 'admin', permissions: ['nav.admin'] }))
    expect(await screen.findByText('Действительна')).toBeVisible()
  })

  it('publishes a successful integration mutation after a cold bootstrap failure without reloading', async () => {
    const actor = userEvent.setup()
    setup({}, [], ['nav.admin'], '/admin/settings', 'admin', () => {
      vi.mocked(api.integrationSettings).mockRejectedValue(new Error('bootstrap offline'))
      vi.spyOn(api, 'checkEmergencyCookie').mockResolvedValue(settings({
        emergency_cookie_status: 'valid', emergency_cookie_valid: true,
        emergency_cookie_checked_robot: '447',
      }))
    })
    await screen.findByText(/Не удалось загрузить: Интеграции/)
    await actor.click(screen.getByRole('button', { name: 'Проверить текущую' }))
    expect(await screen.findByText('Текущая cookie проверена')).toBeVisible()
    expect(screen.getByText('Действительна')).toBeVisible()
    expect(screen.getByText(/робот 447/)).toBeVisible()
    expect(screen.queryByText(/Не удалось загрузить: Интеграции/)).not.toBeInTheDocument()
    expect(api.integrationSettings).toHaveBeenCalledTimes(1)
  })

  it('does not turn unknown protection into editable false after screenshot-guard failure', async () => {
    const actor = userEvent.setup()
    const authoritative = settings({ emergency_cookie_status: 'valid', emergency_cookie_valid: true })
    setup({}, [], ['nav.admin'], '/admin/settings', 'admin', () => {
      vi.mocked(api.screenshotGuardSettings).mockRejectedValue(new Error('protection offline'))
    })
    const mutate = vi.spyOn(api, 'updateScreenshotGuardSettings')
    vi.spyOn(api, 'checkEmergencyCookie').mockResolvedValue(authoritative)
    const heading = await screen.findByRole('heading', { name: 'Защита от скриншотов' })
    const protection = within(heading.closest('section')!)
    expect(protection.queryAllByRole('checkbox')).toHaveLength(0)
    expect(protection.getByRole('status')).toHaveTextContent('Состояние защиты не загружено')
    await actor.click(screen.getByRole('button', { name: 'Проверить текущую' }))
    expect(await screen.findByText('Действительна')).toBeVisible()
    expect(protection.queryAllByRole('checkbox')).toHaveLength(0)
    expect(mutate).not.toHaveBeenCalled()
    const pending = deferred<Awaited<ReturnType<typeof api.screenshotGuardSettings>>>()
    vi.mocked(api.integrationSettings).mockResolvedValue(authoritative)
    vi.mocked(api.screenshotGuardSettings).mockReturnValue(pending.promise)
    await actor.click(protection.getByRole('button', { name: 'Повторить загрузку настроек' }))
    expect(protection.getByRole('status')).toHaveTextContent('Загрузка состояния защиты')
    expect(protection.queryAllByRole('checkbox')).toHaveLength(0)
    await act(async () => pending.resolve({ operator: true, mechanic: false, driver: false, admin: false, royal: false }))
    const toggle = await screen.findByRole('checkbox', { name: 'Запрет скриншотов — Оператор' })
    expect(toggle).toBeChecked()
    expect(toggle).toBeEnabled()
    expect(screen.getByText('Действительна')).toBeVisible()
  })

  it('does not reuse settings or a pending mutation in a new user context', async () => {
    const actor = userEvent.setup()
    const mutation = deferred<IntegrationSettings>()
    vi.spyOn(api, 'checkEmergencyCookie').mockReturnValue(mutation.promise)
    const view = setup({ emergency_cookie_status: 'valid', emergency_cookie_valid: true })
    await screen.findByText('Действительна')
    await actor.click(screen.getByRole('button', { name: 'Проверить текущую' }))
    const next = deferred<IntegrationSettings>()
    vi.mocked(api.integrationSettings).mockReturnValue(next.promise)
    view.rerenderAuth(testUser({ id: 2, role: 'admin', permissions: ['nav.admin'] }))
    expect(screen.queryByText('Действительна')).not.toBeInTheDocument()
    await act(async () => mutation.resolve(settings({ emergency_cookie_status: 'invalid' })))
    expect(screen.queryByText('Недействительна')).not.toBeInTheDocument()
    await act(async () => next.resolve(settings()))
    expect(await screen.findByText('Не проверена')).toBeVisible()
  })

  it('does not start an old-context bootstrap when a policy mutation completes after switching user', async () => {
    const actor = userEvent.setup()
    const pending = deferred<Awaited<ReturnType<typeof api.updateTrackerPolicy>>>()
    vi.spyOn(api, 'updateTrackerPolicy').mockReturnValue(pending.promise)
    const view = setup()
    await actor.click(await screen.findByRole('checkbox', { name: 'Оператор видит неразмеченные тикеты' }))
    view.rerenderAuth(testUser({ id: 2, role: 'admin', permissions: ['nav.admin'] }))
    await screen.findByText('Не проверена')
    const calls = vi.mocked(api.integrationSettings).mock.calls.length
    await act(async () => pending.resolve({
      operator_show_untagged: true, operator_show_raw: false,
      operator_show_firmware_profile: false, mechanic_can_write: false,
    }))
    expect(api.integrationSettings).toHaveBeenCalledTimes(calls)
  })

  it('lets Tracker save while Emergency remains pending', async () => {
    const actor = userEvent.setup()
    const pending = deferred<IntegrationSettings>()
    vi.spyOn(api, 'checkEmergencyCookie').mockReturnValue(pending.promise)
    vi.spyOn(api, 'setTrackerToken').mockResolvedValue(settings({ tracker_token_masked: 'configured' }))
    setup()
    await actor.click(await screen.findByRole('button', { name: 'Проверить текущую' }))
    await actor.type(screen.getByLabelText('Tracker OAuth-токен'), 'tracker-fixture')
    expect(screen.getByRole('button', { name: 'Сохранить токен' })).toBeEnabled()
    await actor.click(screen.getByRole('button', { name: 'Сохранить токен' }))
    expect(await screen.findByText('Токен Tracker сохранён')).toBeVisible()
    expect(screen.getByLabelText('Tracker OAuth-токен')).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Проверить текущую' })).toBeDisabled()
    await act(async () => pending.resolve(settings({ emergency_cookie_status: 'valid' })))
  })

  it('loads a fresh bootstrap after changing park without reusing settings or input', async () => {
    const actor = userEvent.setup()
    const first = setup({ emergency_cookie_status: 'valid', emergency_cookie_valid: true })
    await screen.findByText('Действительна')
    first.unmount()
    const parks = [
      { id: 7, name: 'Северный', timezone: 'Europe/Moscow', tag: 'north', is_active: true },
      { id: 9, name: 'Южный', timezone: 'Europe/Moscow', tag: 'south', is_active: true },
    ]
    vi.mocked(api.parks).mockResolvedValue(parks)
    renderApp('/admin/settings?park=7', testUser({ role: 'admin', permissions: ['nav.admin'], parks }))
    await screen.findByRole('button', { name: 'Сменить парк' })
    await screen.findByLabelText('Cookie диагностики робота')
    await screen.findByText('Действительна')
    await actor.type(screen.getByLabelText('Cookie диагностики робота'), 'candidate-fixture')
    const next = deferred<IntegrationSettings>()
    vi.mocked(api.integrationSettings).mockReturnValue(next.promise)
    await actor.click(screen.getByRole('button', { name: 'Сменить парк' }))
    await actor.click(screen.getByRole('option', { name: 'Южный' }))
    expect(screen.getByTestId('location')).toHaveTextContent('park=9')
    expect(screen.queryByText('Действительна')).not.toBeInTheDocument()
    await act(async () => next.resolve(settings({ emergency_cookie_status: 'invalid' })))
    expect(await screen.findByText('Недействительна')).toBeVisible()
    expect(screen.getByLabelText('Cookie диагностики робота')).toHaveValue('')
  })

  it('asks for a robot when no saved probe is available', async () => {
    const actor = userEvent.setup()
    vi.spyOn(api, 'checkEmergencyCookie').mockRejectedValue(new ApiError(422, 'emergency_probe_required'))
    setup()
    await actor.click(await screen.findByRole('button', { name: 'Проверить текущую' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/Укажите номер робота/)
    expect(screen.getByLabelText('Робот для проверки')).toBeEnabled()
  })

  it('preserves park edits when an integration updates the bootstrap cache', async () => {
    const actor = userEvent.setup()
    const pending = deferred<IntegrationSettings>()
    vi.spyOn(api, 'checkEmergencyCookie').mockReturnValue(pending.promise)
    setup({}, [{ id: 7, name: 'Северный', timezone: 'Europe/Moscow', tag: 'north', is_active: true }], ['nav.admin', 'parks.manage'])
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('park=7'))
    await actor.click(await screen.findByRole('button', { name: 'Проверить текущую' }))
    await actor.click(screen.getByRole('link', { name: 'Парки' }))
    await actor.click(await screen.findByRole('button', { name: 'Открыть парк Северный' }))
    const name = screen.getAllByLabelText('Название').at(-1)!
    await actor.clear(name)
    await actor.type(name, 'Новое название')
    await act(async () => pending.resolve(settings({ emergency_cookie_status: 'valid' })))
    expect(name).toHaveValue('Новое название')
  })

  it('saves a cookie without requiring or running a robot check', async () => {
    const user = userEvent.setup()
    const setCookie = vi.spyOn(api, 'setEmergencyCookie').mockResolvedValue(settings({
      emergency_cookie_status: 'unchecked',
      emergency_cookie_valid: null,
    }))
    setup()

    const save = await screen.findByRole('button', { name: 'Сохранить cookie' })
    const cookie = screen.getByLabelText('Cookie диагностики робота')
    expect(save).toBeDisabled()

    await user.type(cookie, 'candidate-cookie')
    expect(save).toBeEnabled()
    await user.click(save)

    await waitFor(() => expect(setCookie).toHaveBeenCalledWith('candidate-cookie'))
    expect(await screen.findByText('Cookie сохранена')).toBeVisible()
  })

  it('keeps a rejected candidate cookie available for correction', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'setEmergencyCookie').mockRejectedValue(
      new ApiError(401, 'emergency_cookie_invalid'),
    )
    setup()

    await user.type(await screen.findByLabelText('Cookie диагностики робота'), 'candidate-cookie')
    await user.type(screen.getByLabelText('Робот для проверки'), '447')
    await user.click(screen.getByRole('button', { name: 'Сохранить cookie' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Cookie проверки робота отклонена. Скопируйте свежую сессию и повторите.',
    )
    expect(screen.getByLabelText('Cookie диагностики робота')).toHaveValue('candidate-cookie')
  })

  it('keeps cookie validation available while the Tracker token is saving', async () => {
    const user = userEvent.setup()
    const pending = deferred<IntegrationSettings>()
    vi.spyOn(api, 'setTrackerToken').mockReturnValue(pending.promise)
    setup()

    await user.type(await screen.findByLabelText('Tracker OAuth-токен'), 'tracker-token')
    await user.type(screen.getByLabelText('Cookie диагностики робота'), 'candidate-cookie')
    await user.type(screen.getByLabelText('Робот для проверки'), '447')
    await user.click(screen.getByRole('button', { name: 'Сохранить токен' }))

    await waitFor(() => expect(api.setTrackerToken).toHaveBeenCalledWith('tracker-token'))
    expect(screen.getByRole('button', { name: 'Сохранить cookie' })).toBeEnabled()
    pending.resolve(settings())
  })

  it('keeps fresh cookie metadata when a delayed Tracker response arrives last', async () => {
    const user = userEvent.setup()
    const tracker = deferred<IntegrationSettings>()
    const emergency = deferred<IntegrationSettings>()
    vi.spyOn(api, 'setTrackerToken').mockReturnValue(tracker.promise)
    vi.spyOn(api, 'checkEmergencyCookie').mockReturnValue(emergency.promise)
    setup()

    await user.type(await screen.findByLabelText('Tracker OAuth-токен'), 'tracker-token')
    await user.click(screen.getByRole('button', { name: 'Сохранить токен' }))
    await user.click(screen.getByRole('button', { name: 'Проверить текущую' }))
    await waitFor(() => expect(api.checkEmergencyCookie).toHaveBeenCalledWith(undefined))

    emergency.resolve(settings({
      emergency_cookie_status: 'valid',
      emergency_cookie_valid: true,
      emergency_cookie_checked_at: '2026-09-06T10:00:00Z',
      emergency_cookie_checked_robot: '447',
    }))
    expect(await screen.findByText('Действительна')).toBeVisible()

    tracker.resolve(settings({ tracker_token_masked: 'tracker-masked' }))

    await waitFor(() => expect(screen.getByText('работает')).toBeVisible())
    expect(screen.getByText('Действительна')).toBeVisible()
    expect(screen.getByText(/робот 447/i)).toBeVisible()
  })

  it('keeps fresh Tracker metadata when a delayed cookie response arrives last', async () => {
    const user = userEvent.setup()
    const tracker = deferred<IntegrationSettings>()
    const emergency = deferred<IntegrationSettings>()
    vi.spyOn(api, 'setTrackerToken').mockReturnValue(tracker.promise)
    vi.spyOn(api, 'checkEmergencyCookie').mockReturnValue(emergency.promise)
    setup()

    await user.type(await screen.findByLabelText('Tracker OAuth-токен'), 'tracker-token')
    await user.click(screen.getByRole('button', { name: 'Сохранить токен' }))
    await user.click(screen.getByRole('button', { name: 'Проверить текущую' }))
    await waitFor(() => expect(api.checkEmergencyCookie).toHaveBeenCalledWith(undefined))

    tracker.resolve(settings({ tracker_token_masked: 'tracker-masked' }))
    expect(await screen.findByText('работает')).toBeVisible()

    emergency.resolve(settings({
      emergency_cookie_status: 'valid',
      emergency_cookie_valid: true,
      emergency_cookie_checked_at: '2026-09-06T10:00:00Z',
      emergency_cookie_checked_robot: '447',
    }))

    expect(await screen.findByText('Действительна')).toBeVisible()
    expect(screen.getByText('работает')).toBeVisible()
  })
})
