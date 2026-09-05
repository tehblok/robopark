import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, type IntegrationSettings } from '../api'
import { resourceStore } from '../lib/resource'
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

function setup(validation: Partial<IntegrationSettings> = {}) {
  vi.spyOn(api, 'parks').mockResolvedValue([])
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
  renderApp('/admin/settings', testUser({ role: 'admin', permissions: ['nav.admin'] }))
}

describe('Admin Emergency cookie validation', () => {
  beforeEach(() => {
    installMatchMedia()
  })

  afterEach(() => {
    resourceStore.clearAll()
    vi.restoreAllMocks()
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

  it('requires both a cookie and robot before saving and checking', async () => {
    const user = userEvent.setup()
    const setCookie = vi.spyOn(api, 'setEmergencyCookie').mockResolvedValue(settings({
      emergency_cookie_status: 'valid',
      emergency_cookie_valid: true,
      emergency_cookie_checked_robot: '447',
      emergency_cookie_checked_at: '2026-09-06T09:00:00Z',
    }))
    setup()

    const save = await screen.findByRole('button', { name: 'Сохранить и проверить' })
    const cookie = screen.getByLabelText('Cookie диагностики робота')
    const robot = screen.getByLabelText('Робот для проверки')
    expect(save).toBeDisabled()

    await user.type(cookie, 'candidate-cookie')
    expect(save).toBeDisabled()
    await user.type(robot, '447')
    expect(save).toBeEnabled()
    await user.click(save)

    await waitFor(() => expect(setCookie).toHaveBeenCalledWith('candidate-cookie', '447'))
  })

  it('keeps a rejected candidate cookie available for correction', async () => {
    const user = userEvent.setup()
    vi.spyOn(api, 'setEmergencyCookie').mockRejectedValue(
      new ApiError(401, 'emergency_cookie_invalid'),
    )
    setup()

    await user.type(await screen.findByLabelText('Cookie диагностики робота'), 'candidate-cookie')
    await user.type(screen.getByLabelText('Робот для проверки'), '447')
    await user.click(screen.getByRole('button', { name: 'Сохранить и проверить' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Интеграция проверки робота требует внимания.',
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
    expect(screen.getByRole('button', { name: 'Сохранить и проверить' })).toBeEnabled()
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
