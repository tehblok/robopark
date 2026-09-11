import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type InventoryOverview, type Park, type User } from '../../api'
import { AuthContext } from '../../auth-context'
import { ParkScopeContext } from '../../app/park/parkScope'
import { InventoryPage } from './InventoryPage'

const park: Park = { id: 7, name: 'Север', tag: 'North', is_active: true }
const overview: InventoryOverview = {
  park_id: 7,
  component_count: 3,
  part_count: 12,
  low_stock_count: 2,
  out_of_stock_count: 1,
  components: [],
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function LocationProbe() {
  const location = useLocation()
  return <output aria-label="Адрес">{location.pathname}{location.search}</output>
}

function renderInventory(path: string, role: User['role'] = 'mechanic') {
  const user: User = {
    id: 1,
    username: role,
    role,
    access_status: 'approved',
    permissions: ['nav.inventory'],
    parks: [park],
  }
  const client = { inventory: vi.fn(async () => overview) }
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthContext.Provider value={{ user, loading: false, login: vi.fn(), refreshUser: vi.fn(), logout: vi.fn() }}>
        <ParkScopeContext.Provider value={{ parkId: park.id, selectedPark: park, parks: [park], loading: false, locked: role === 'mechanic', setParkId: vi.fn(), refreshParks: vi.fn() }}>
          <InventoryPage apiClient={client as never} />
          <LocationProbe />
        </ParkScopeContext.Provider>
      </AuthContext.Provider>
    </MemoryRouter>,
  )
}

describe('inventory URL tabs', () => {
  it('selects the URL workflow, preserves park and mounts only the active panel', async () => {
    renderInventory('/inventory?park=7&view=receipts')

    expect(await screen.findByRole('tab', { name: 'Поставки' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toHaveTextContent('Поставки')
    expect(screen.queryByText('Склад пуст')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('tab', { name: 'Инвентаризация' }))

    expect(screen.getByLabelText('Адрес')).toHaveTextContent('/inventory?park=7&view=counts')
    expect(screen.getByRole('tabpanel')).toHaveTextContent('Инвентаризация')
    expect(screen.queryByText('Поставки будут')).not.toBeInTheDocument()
  })

  it('canonicalizes an invalid view to parts without dropping other query parameters', async () => {
    renderInventory('/inventory?park=7&view=unknown&source=qr')

    expect(await screen.findByRole('tab', { name: 'Запчасти' })).toHaveAttribute('aria-selected', 'true')
    await waitFor(() => expect(screen.getByLabelText('Адрес')).toHaveTextContent('/inventory?park=7&view=parts&source=qr'))
  })

  it.each(['mechanic', 'operator', 'admin', 'royal'] as const)('exposes all park workflows to %s with scoped management copy', async (role) => {
    renderInventory('/inventory?park=7&view=manage', role)

    expect(await screen.findAllByRole('tab')).toHaveLength(5)
    expect(screen.getByRole('tabpanel')).toHaveTextContent(
      role === 'admin' || role === 'royal' ? 'Глобальный каталог' : 'Настройки склада парка',
    )
    if (role === 'mechanic' || role === 'operator') {
      expect(screen.queryByText('Все парки')).not.toBeInTheDocument()
    }
  })

  it('supports keyboard tab selection without overflowing a 390px page', async () => {
    vi.stubGlobal('innerWidth', 390)
    renderInventory('/inventory?park=7&view=parts')
    const parts = await screen.findByRole('tab', { name: 'Запчасти' })

    parts.focus()
    fireEvent.keyDown(parts, { key: 'ArrowRight' })

    expect(screen.getByRole('tab', { name: 'Поставки' })).toHaveFocus()
    expect(screen.getByLabelText('Адрес')).toHaveTextContent('view=receipts')
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth)
  })
})

describe('inventory API contracts', () => {
  it('serializes catalog, stock, receipt and count requests to the committed backend routes', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ items: [], limit: 25, offset: 0, total: 0 }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    vi.stubGlobal('fetch', fetchMock)
    await api.searchInventory({ parkId: 7, query: 'ABC', componentId: 2, stockFilter: 'below_minimum', limit: 25, offset: 0 })
    await api.updateInventoryStock(7, 3, { minimum_quantity: 2, location: 'A-1', is_active: true })
    await api.inventoryReceipts(7, { query: 'DOC', limit: 25, offset: 0 })
    await api.reverseInventoryReceipt(7, 11, 'duplicate')
    await api.inventoryCounts(7, { query: 'September', limit: 25, offset: 0 })
    await api.createInventoryCount(7, { name: 'September', scope: { kind: 'component', component_id: 2 } })

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/inventory/catalog/search?park_id=7&q=ABC&component_id=2&stock_filter=below_minimum&limit=25&offset=0',
      '/api/inventory/parks/7/stocks/3',
      '/api/inventory/parks/7/receipts?q=DOC&limit=25&offset=0',
      '/api/inventory/parks/7/receipts/11/reverse',
      '/api/inventory/parks/7/counts?q=September&limit=25&offset=0',
      '/api/inventory/parks/7/counts',
    ])
    expect(JSON.parse(String(fetchMock.mock.calls[3][1]?.body))).toEqual({ reason: 'duplicate' })
    expect(JSON.parse(String(fetchMock.mock.calls[5][1]?.body))).toEqual({ name: 'September', scope: { kind: 'component', component_id: 2 } })
  })

  it('downloads only the requested export content type and always revokes its object URL', async () => {
    const blob = new Blob(['inventory'], { type: 'text/csv' })
    vi.stubGlobal('fetch', vi.fn(async () => new Response(blob, {
      status: 200,
      headers: { 'Content-Type': 'text/csv; charset=utf-8' },
    })))
    const createObjectURL = vi.fn(() => 'blob:inventory')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => { throw new Error('click-failed') })

    await expect(api.downloadInventoryExport({ parkId: 7, format: 'csv' })).rejects.toThrow('click-failed')

    expect(fetch).toHaveBeenCalledWith('/api/inventory/export?park_id=7&format=csv', expect.objectContaining({ credentials: 'include' }))
    expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob))
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:inventory')
  })

  it('rejects an unexpected export content type before creating a download URL', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>login</html>', {
      status: 200,
      headers: { 'Content-Type': 'text/html' },
    })))
    const createObjectURL = vi.fn()
    vi.stubGlobal('URL', { ...URL, createObjectURL, revokeObjectURL: vi.fn() })

    await expect(api.downloadInventoryExport({ scope: 'all', format: 'xlsx' })).rejects.toMatchObject({
      detail: 'inventory_export_content_type_invalid',
    })
    expect(createObjectURL).not.toHaveBeenCalled()
  })
})
