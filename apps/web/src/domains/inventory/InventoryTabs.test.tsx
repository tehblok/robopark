import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, inventoryErrorDetail, type InventoryOverview, type Park, type User } from '../../api'
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

function renderInventory(path: string, role: User['role'] = 'mechanic', inventory = vi.fn(async () => overview)) {
  const user: User = {
    id: 1,
    username: role,
    role,
    access_status: 'approved',
    permissions: ['nav.inventory'],
    parks: [park],
  }
  const client = { inventory }
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

  it('renders non-parts workflows without requesting or depending on legacy overview', async () => {
    const inventory = vi.fn(async () => { throw new ApiError(503, 'legacy_unavailable') })
    renderInventory('/inventory?park=7&view=receipts', 'mechanic', inventory)

    expect(await screen.findByRole('tabpanel')).toHaveTextContent('Поставки')
    expect(screen.getByRole('tablist', { name: 'Разделы склада' })).toBeVisible()
    expect(inventory).not.toHaveBeenCalled()
    expect(screen.queryByText('Сервис временно недоступен')).not.toBeInTheDocument()
  })

  it('keeps parts loading failures inside the active panel while tabs stay usable', async () => {
    renderInventory('/inventory?park=7&view=parts', 'mechanic', vi.fn(async () => { throw new ApiError(503) }))

    expect(await screen.findByRole('tabpanel')).toHaveTextContent('Сервис временно недоступен')
    await userEvent.click(screen.getByRole('tab', { name: 'Выгрузка' }))
    expect(screen.getByRole('tabpanel')).toHaveTextContent('Выгрузка парка Север')
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

  it('supports keyboard tab selection and exposes every controlled panel', async () => {
    renderInventory('/inventory?park=7&view=parts')
    const parts = await screen.findByRole('tab', { name: 'Запчасти' })

    parts.focus()
    fireEvent.keyDown(parts, { key: 'ArrowRight' })

    expect(screen.getByRole('tab', { name: 'Поставки' })).toHaveFocus()
    expect(screen.getByLabelText('Адрес')).toHaveTextContent('view=receipts')
    for (const tab of screen.getAllByRole('tab')) {
      expect(document.getElementById(tab.getAttribute('aria-controls')!)).toBeInTheDocument()
    }
    expect(document.querySelectorAll('[data-inventory-workflow]')).toHaveLength(1)
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
    await api.updateInventoryStock(7, 3, { minimum_quantity: '2', location: 'A-1', is_active: true })
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

  it('preserves structured inventory errors for typed conflict handling', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      '{"detail":{"code":"inventory_count_stale","conflicts":[{"catalog_part_id":3,"expected_quantity":9007199254740993,"current_quantity":9223372036854775807}]}}',
      { status: 409, headers: { 'Content-Type': 'application/json' } },
    )))

    const failure = await api.postInventoryCount(7, 11).catch(error => error) as ApiError & {
      structuredDetail?: unknown
    }

    expect(failure.detail).toBeNull()
    expect(failure.structuredDetail).toEqual({
      code: 'inventory_count_stale',
      conflicts: [{ catalog_part_id: 3, expected_quantity: '9007199254740993', current_quantity: '9223372036854775807' }],
    })
    expect(inventoryErrorDetail(failure)?.code).toBe('inventory_count_stale')
  })

  it('keeps existing string error details compatible with current UX consumers', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"inventory_count_not_found"}', {
      status: 404,
      headers: { 'Content-Type': 'application/json' },
    })))

    const failure = await api.postInventoryCount(7, 404).catch(error => error) as ApiError

    expect(failure.detail).toBe('inventory_count_not_found')
    expect(failure.structuredDetail).toBeNull()
    expect(inventoryErrorDetail(failure)).toBeNull()
  })

  it('parses and serializes inventory int64 quantities without precision loss', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(
        '{"items":[{"id":3,"component_id":2,"component_name":"Wheel","name":"Tyre","article":"WH-1","is_active":true,"has_photo":false,"quantity":9007199254740993,"minimum_quantity":9223372036854775807,"location":"A-1","stock_is_active":true}],"limit":50,"offset":0,"total":1}',
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ))
      .mockResolvedValueOnce(new Response(
        '{"park_id":7,"catalog_part_id":3,"quantity":9007199254740993,"minimum_quantity":9223372036854775807,"location":"A-1","is_active":true,"version":9223372036854775807}',
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ))
      .mockResolvedValueOnce(new Response(
        '{"id":11,"park_id":7,"supplier":null,"document_number":null,"received_on":"2026-09-12","comment":null,"status":"draft","created_by":1,"posted_by":null,"created_at":"2026-09-12T00:00:00Z","posted_at":null,"lines":[{"id":12,"catalog_part_id":3,"quantity":9223372036854775807,"note":null}]}',
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ))
      .mockResolvedValueOnce(new Response(
        '{"id":21,"park_id":7,"name":"September","status":"draft","created_by":1,"posted_by":null,"created_at":"2026-09-12T00:00:00Z","posted_at":null,"lines":[{"id":22,"catalog_part_id":3,"expected_quantity":9007199254740993,"actual_quantity":9223372036854775807,"difference":9214364837600034814,"comment":null}]}',
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ))
    vi.stubGlobal('fetch', fetchMock)

    const search = await api.searchInventory({ parkId: 7 })
    const stock = await api.updateInventoryStock(7, 3, { minimum_quantity: '9223372036854775807', location: 'A-1', is_active: true })
    const receipt = await api.createInventoryReceipt(7, {
      received_on: '2026-09-12',
      lines: [{ catalog_part_id: 3, quantity: '9223372036854775807' }],
    })
    const count = await api.updateInventoryCount(7, 21, [{ catalog_part_id: 3, actual_quantity: '9223372036854775807' }])

    expect(search.items[0]).toMatchObject({ quantity: '9007199254740993', minimum_quantity: '9223372036854775807' })
    expect(stock).toMatchObject({ quantity: '9007199254740993', minimum_quantity: '9223372036854775807', version: '9223372036854775807' })
    expect(receipt.lines[0].quantity).toBe('9223372036854775807')
    expect(count.lines[0]).toMatchObject({
      expected_quantity: '9007199254740993',
      actual_quantity: '9223372036854775807',
      difference: '9214364837600034814',
    })
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({ minimum_quantity: '9223372036854775807', location: 'A-1', is_active: true })
    expect(JSON.parse(String(fetchMock.mock.calls[2][1]?.body)).lines[0].quantity).toBe('9223372036854775807')
    expect(JSON.parse(String(fetchMock.mock.calls[3][1]?.body)).lines[0].actual_quantity).toBe('9223372036854775807')
  })
})
