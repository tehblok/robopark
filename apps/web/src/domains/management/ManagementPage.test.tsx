import { screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../api'
import { installMatchMedia, renderApp, testUser } from '../../test/renderApp'

const north = { id: 7, name: 'Северный', tag: 'north', is_active: true }

describe('Management routes', () => {
  beforeEach(() => installMatchMedia())
  afterEach(() => vi.restoreAllMocks())

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
    expect(screen.getAllByRole('link', { name: 'Открыть' })
      .map((link) => link.getAttribute('href'))).toContain('/admin/users')
    expect(screen.queryByText('Роли и доступы')).not.toBeInTheDocument()
  })

  it('normalizes a forbidden settings tab to parks without loading integration controls', async () => {
    const integration = vi.spyOn(api, 'integrationSettings').mockRejectedValue(new Error('forbidden'))
    vi.spyOn(api, 'parks').mockResolvedValue([north])
    renderApp('/admin/settings?tab=ops', testUser({
      role: 'field_lead', permissions: ['parks.manage'], parks: [north],
    }))

    expect(await screen.findByRole('heading', { name: 'Администрирование' })).toBeVisible()
    expect(screen.getByTestId('location')).toHaveTextContent('/admin/settings?tab=parks')
    expect(integration).not.toHaveBeenCalled()
    expect(screen.queryByRole('heading', { name: 'Секреты' })).not.toBeInTheDocument()
  })
})
