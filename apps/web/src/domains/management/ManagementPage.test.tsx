import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../api'
import { installMatchMedia, renderApp, testUser } from '../../test/renderApp'
import { resourceStore } from '../../lib/resource'

const north = { id: 7, name: 'Северный', tag: 'north', is_active: true }

describe('Management routes', () => {
  beforeEach(() => installMatchMedia())
  afterEach(() => { resourceStore.clearAll(); vi.restoreAllMocks() })

  it('keeps the management parent active and exposes stable subsection navigation on a nested page', async () => {
    vi.spyOn(api, 'parks').mockResolvedValue([north])
    vi.spyOn(api, 'adminRoles').mockResolvedValue([])
    vi.spyOn(api, 'adminRolePermissionCatalog').mockResolvedValue([])
    renderApp('/admin/roles?park=7', testUser({
      role: 'royal', permissions: ['nav.admin', 'roles.manage', 'users.manage', 'parks.manage'], parks: [north],
    }))
    const navigation = screen.getAllByRole('navigation', { name: 'Основная навигация' })[0]
    expect(within(navigation).getByRole('link', { name: 'Управление' })).toHaveAttribute('aria-current', 'page')
    const sections = await screen.findByRole('navigation', { name: 'Разделы управления' })
    expect(screen.getByRole('combobox', { name: 'Раздел управления' })).toHaveValue('/admin/roles?park=7')
    expect(within(sections).getByRole('link', { name: 'Роли и доступы' })).toHaveAttribute('aria-current', 'page')
    await waitFor(() => expect(within(sections).getByRole('link', { name: 'Пользователи' })).toHaveAttribute('href', '/admin/users?park=7'))
  })

  it.each([
    ['/admin/users?tab=parks', 'Пользователи'],
    ['/admin/roles?tab=parks', 'Роли и доступы'],
  ])('ignores a foreign settings tab when highlighting %s', async (url, label) => {
    vi.spyOn(api, 'parks').mockResolvedValue([north])
    vi.spyOn(api, 'adminUsers').mockResolvedValue([])
    vi.spyOn(api, 'adminRoles').mockResolvedValue([])
    vi.spyOn(api, 'adminRolePermissionCatalog').mockResolvedValue([])
    renderApp(url, testUser({ role: 'royal', permissions: ['users.manage', 'roles.manage'], parks: [north] }))
    const sections = await screen.findByRole('navigation', { name: 'Разделы управления' })
    expect(within(sections).getByRole('link', { name: label })).toHaveAttribute('aria-current', 'page')
  })

  it('lets a granular users manager open accounts even when integrations are unavailable', async () => {
    const integration = vi.spyOn(api, 'integrationSettings').mockRejectedValue(new Error('offline'))
    vi.spyOn(api, 'parks').mockResolvedValue([north])
    vi.spyOn(api, 'adminUsers').mockResolvedValue([])
    vi.spyOn(api, 'adminRoles').mockResolvedValue([])
    vi.spyOn(api, 'adminRolePermissionCatalog').mockResolvedValue([])

    renderApp('/admin/users', testUser({
      role: 'field_lead',
      permissions: ['users.manage'],
      parks: [north],
    }))

    expect(await screen.findByRole('heading', { name: 'Пользователи' })).toBeVisible()
    expect(await screen.findByRole('heading', { name: 'Аккаунты' })).toBeVisible()
    expect(integration).not.toHaveBeenCalled()
  })

  it('shows only granted management modules on the hub', async () => {
    renderApp('/admin', testUser({
      role: 'field_lead',
      permissions: ['users.manage', 'parks.manage'],
      parks: [north],
    }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Управление' })).toBeVisible()
    await waitFor(() => expect(screen.getAllByRole('link', { name: 'Открыть' })
      .map((link) => link.getAttribute('href'))).toContain('/admin/users?park=7'))
    expect(screen.queryByText('Роли и доступы')).not.toBeInTheDocument()
  })

  it('normalizes a forbidden settings tab to parks without loading integration controls', async () => {
    const integration = vi.spyOn(api, 'integrationSettings').mockRejectedValue(new Error('forbidden'))
    vi.spyOn(api, 'parks').mockResolvedValue([north])
    renderApp('/admin/settings?tab=ops', testUser({
      role: 'field_lead', permissions: ['parks.manage'], parks: [north],
    }))

    expect(await screen.findByRole('heading', { name: 'Администрирование' })).toBeVisible()
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/admin/settings?tab=parks'))
    expect(integration).not.toHaveBeenCalled()
    expect(screen.queryByRole('heading', { name: 'Секреты' })).not.toBeInTheDocument()
  })

  it('uses Russian robot-check wording throughout Management integration settings', async () => {
    vi.spyOn(api, 'parks').mockResolvedValue([north])
    vi.spyOn(api, 'adminParkRequests').mockResolvedValue([])
    vi.spyOn(api, 'integrationSettings').mockResolvedValue({
      tracker_token_masked: null,
      tracker_token_updated_at: null,
      emergency_cookie_masked: null,
      emergency_cookie_updated_at: null,
      emergency_cookie_valid: false,
      emergency_cookie_status: 'unchecked',
      emergency_cookie_checked_at: null,
      emergency_cookie_checked_robot: null,
    })
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

    renderApp('/admin/settings', testUser({
      role: 'admin', permissions: ['nav.admin'], parks: [north],
    }))

    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('park=7'))
    expect(await screen.findByLabelText('Cookie диагностики робота')).toBeVisible()
    expect(screen.getByRole('link', { name: 'Проверка робота' })).toHaveAttribute('href', '/emergency')
    expect(screen.getByRole('link', { name: 'Настройки проверки робота' })).toHaveAttribute('href', '/admin/emergency/config')
    expect(document.body).not.toHaveTextContent(/Аварийный режим/i)
    expect(screen.queryByText(/^(?:Emergency|Конфиг Emergency|Разделы Emergency)/i)).not.toBeInTheDocument()
  })

  it('switches settings subsections using URL links without reverting to the old tab', async () => {
    vi.spyOn(api, 'parks').mockResolvedValue([north])
    vi.spyOn(api, 'adminParkRequests').mockResolvedValue([])
    vi.spyOn(api, 'integrationSettings').mockResolvedValue({ tracker_token_masked: null, tracker_token_updated_at: null, emergency_cookie_masked: null, emergency_cookie_updated_at: null, emergency_cookie_valid: null, emergency_cookie_status: 'unchecked', emergency_cookie_checked_at: null, emergency_cookie_checked_robot: null })
    vi.spyOn(api, 'trackerPolicy').mockResolvedValue({ operator_show_untagged: false, operator_show_raw: false, operator_show_firmware_profile: false, mechanic_can_write: false })
    vi.spyOn(api, 'screenshotGuardSettings').mockResolvedValue({ operator: false, mechanic: false, driver: false, admin: false, royal: false })
    vi.spyOn(api, 'adminUsers').mockResolvedValue([])
    await act(async () => {
      renderApp('/admin/settings?park=7', testUser({ role: 'admin', permissions: ['nav.admin', 'parks.manage'], parks: [north] }))
    })
    await screen.findByText('Не проверена')
    fireEvent.click(within(screen.getByRole('navigation', { name: 'Разделы управления' })).getByRole('link', { name: 'Парки' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Добавить парк' }))
    expect(await screen.findByRole('heading', { name: 'Новый парк' })).toBeVisible()
    expect(screen.getByTestId('location')).toHaveTextContent('/admin/settings?park=7&tab=parks')
    fireEvent.click(within(screen.getByRole('navigation', { name: 'Разделы управления' })).getByRole('link', { name: 'Настройки' }))
    expect(await screen.findByLabelText('Cookie диагностики робота')).toBeVisible()
    expect(screen.getByTestId('location')).toHaveTextContent('/admin/settings?park=7')
  })

  it('uses Russian robot-check wording in the empty configuration editor', async () => {
    vi.spyOn(api, 'adminEmergencySections').mockResolvedValue([])
    renderApp('/admin/emergency/config', testUser({
      permissions: ['nav.admin.emergency'], parks: [north],
    }))

    expect(await screen.findByRole('heading', { name: 'Настройки проверки робота' })).toBeVisible()
    expect(await screen.findByText(
      'Разделы проверки робота ещё не настроены',
      undefined,
      { timeout: 3_000 },
    )).toBeVisible()
    expect(document.body).not.toHaveTextContent(/Аварийный режим/i)
    expect(screen.queryByText(/^(?:Emergency|Конфиг Emergency|Разделы Emergency)/i)).not.toBeInTheDocument()
  })
})

it('Royal tabs move keyboard focus into the system panel and mount operations only when selected', async () => {
  installMatchMedia()
  const health = vi.spyOn(api, 'opsSystemHealth').mockResolvedValue({ version: null, git_sha: null, generated_at: null, overall: 'unknown', checks: [], update: { state: 'unknown', publication: null }, last_backup: { status: 'unknown', completed_at: null } })
  vi.spyOn(api, 'opsAvailableUpdate').mockResolvedValue({ state: 'disabled', checked_at: null, release: null })
  vi.spyOn(api, 'opsJob').mockRejectedValue(new Error('offline'))
  vi.spyOn(api, 'parks').mockResolvedValue([north])
  renderApp('/admin/settings?park=7&tab=parks', testUser({ role: 'royal', permissions: ['parks.manage'], parks: [north] }))
  const parksTab = await screen.findByRole('tab', { name: /Парки/ })
  expect(health).not.toHaveBeenCalled()
  parksTab.focus()
  fireEvent.keyDown(parksTab, { key: 'End' })
  const opsTab = screen.getByRole('tab', { name: 'Система и обновления' })
  expect(opsTab).toHaveFocus()
  expect(await screen.findByRole('tabpanel', { name: 'Система и обновления' })).toBeVisible()
  expect(await screen.findByRole('heading', { name: 'Здоровье системы' })).toBeVisible()
  fireEvent.keyDown(opsTab, { key: 'Home' })
  expect(parksTab).toHaveFocus()
  expect(screen.queryByRole('heading', { name: 'Здоровье системы' })).not.toBeInTheDocument()
  vi.restoreAllMocks()
})
