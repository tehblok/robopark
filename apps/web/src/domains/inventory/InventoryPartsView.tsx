import { type FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import { api, type InventoryCatalogSearchItem, type InventoryPageEnvelope, type InventorySearchParams, type InventoryStockFilter, type InventoryStockView } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { FormField } from '../../design-system/forms/FormField'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { classifyApiError } from '../../shared/api/classifyApiError'
import { InventoryLabels } from './InventoryLabels'
import { inventoryInt64Compare, inventoryQuantityError, isInventoryQuantity } from './inventoryTypes'

type InventoryPartsApi = Pick<typeof api, 'searchInventory' | 'inventoryCatalogComponents' | 'updateInventoryStock' | 'inventoryPartPhotoUrl'>
type PartWorkflowKind = 'stock' | 'label'
type PartWorkflow = { catalogPartId: number; kind: PartWorkflowKind } | null

export type InventoryPartsViewProps = {
  apiClient?: InventoryPartsApi
  debounceMs?: number
  parkId: number
  selectedCatalogPartId?: number | null
  onSelectedCatalogPartIdChange?: (catalogPartId: number | null) => void
}

function StockForm({ apiClient, onSaved, parkId, part }: { apiClient: InventoryPartsApi; onSaved: (stock: InventoryStockView, requestedParkId: number) => void; parkId: number; part: InventoryCatalogSearchItem }) {
  const [minimum, setMinimum] = useState<string>(part.minimum_quantity)
  const [location, setLocation] = useState(part.location ?? '')
  const [active, setActive] = useState(part.stock_is_active)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const mutationGeneration = useRef(0)
  useEffect(() => () => { mutationGeneration.current += 1 }, [])
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!isInventoryQuantity(minimum)) return
    setBusy(true)
    setError('')
    const requestId = ++mutationGeneration.current
    const requestedParkId = parkId
    try {
      const stock = await apiClient.updateInventoryStock(requestedParkId, part.id, { minimum_quantity: minimum, location, is_active: active })
      if (requestId === mutationGeneration.current && stock.park_id === requestedParkId) onSaved(stock, requestedParkId)
    } catch (reason) {
      setError(classifyApiError(reason, 'Не удалось сохранить остаток.').description)
    } finally {
      setBusy(false)
    }
  }
  return <form aria-label="Настройки остатка" className="inventory-card-form" onSubmit={submit}>
    <FormField id={`stock-location-${part.id}`} label="Место"><input value={location} onChange={event => setLocation(event.target.value)} /></FormField>
    <FormField error={inventoryQuantityError(minimum)} id={`stock-minimum-${part.id}`} label="Минимум"><input inputMode="numeric" pattern="[0-9]*" value={minimum} onChange={event => setMinimum(event.target.value)} /></FormField>
    <label className="inventory-checkbox"><input checked={active} type="checkbox" onChange={event => setActive(event.target.checked)} /> Учитывать в складе парка</label>
    <Button busy={busy} disabled={!isInventoryQuantity(minimum)} size="compact" type="submit">Сохранить</Button>
    {error ? <p className="form-error" role="alert">{error}</p> : null}
  </form>
}

export function InventoryPartsView({ apiClient = api, debounceMs = 300, parkId, selectedCatalogPartId, onSelectedCatalogPartIdChange }: InventoryPartsViewProps) {
  const [query, setQuery] = useState('')
  const [componentId, setComponentId] = useState<number | undefined>()
  const [stockFilter, setStockFilter] = useState<InventoryStockFilter | undefined>()
  const [offset, setOffset] = useState(0)
  const [result, setResult] = useState<InventoryPageEnvelope<InventoryCatalogSearchItem> | null>(null)
  const [knownComponents, setKnownComponents] = useState<Array<{ id: number; name: string }>>([])
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(true)
  const [internalSelectedId, setInternalSelectedId] = useState<number | null>(null)
  const [workflow, setWorkflow] = useState<PartWorkflow>(null)
  const generation = useRef(0)
  const activeParkId = useRef(parkId)
  useEffect(() => { activeParkId.current = parkId }, [parkId])
  const selectedId = selectedCatalogPartId === undefined ? internalSelectedId : selectedCatalogPartId
  const select = (id: number | null) => {
    if (selectedCatalogPartId === undefined) setInternalSelectedId(id)
    onSelectedCatalogPartIdChange?.(id)
  }
  const params: InventorySearchParams = useMemo(() => ({ parkId, query: query.trim() || undefined, componentId, stockFilter, limit: 25, offset }), [componentId, offset, parkId, query, stockFilter])

  useEffect(() => {
    const requestId = ++generation.current
    setLoading(true)
    setError(null)
    const timer = globalThis.setTimeout(() => {
      apiClient.searchInventory(params).then(value => {
        if (requestId !== generation.current) return
        setResult(value)
        setLoading(false)
      }).catch(reason => {
        if (requestId !== generation.current) return
        setError(reason)
        setLoading(false)
      })
    }, query ? debounceMs : 0)
    return () => { globalThis.clearTimeout(timer); generation.current += 1 }
  }, [apiClient, debounceMs, params, query])

  useEffect(() => {
    const requestedParkId = parkId
    apiClient.inventoryCatalogComponents(parkId, { limit: 200, offset: 0 }).then(value => {
      if (activeParkId.current === requestedParkId) setKnownComponents(value.items.map(item => ({ id: item.id, name: item.name })))
    }).catch(() => { /* Search remains usable if metadata is unavailable. */ })
  }, [apiClient, parkId])

  useEffect(() => {
    setOffset(0)
    setResult(null)
    setKnownComponents([])
    setWorkflow(null)
    select(null)
  // A park change starts a fresh, unselected catalog.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [parkId])

  const failure = error ? classifyApiError(error, 'Не удалось загрузить запчасти.') : null
  const updateLocalStock = (stock: InventoryStockView, requestedParkId: number) => {
    if (requestedParkId !== activeParkId.current || stock.park_id !== requestedParkId) return
    setResult(current => current ? {
    ...current,
    items: current.items.map(item => item.id === stock.catalog_part_id ? { ...item, minimum_quantity: stock.minimum_quantity, location: stock.location, stock_is_active: stock.is_active } : item),
    } : current)
  }
  const openWorkflow = (catalogPartId: number, kind: PartWorkflowKind) => {
    select(catalogPartId)
    setWorkflow({ catalogPartId, kind })
  }
  const print = (item: InventoryCatalogSearchItem) => {
    openWorkflow(item.id, 'label')
    globalThis.setTimeout(() => window.print(), 0)
  }

  return <section className="inventory-catalog-view">
    <FormField id="inventory-part-search" label="Найти запчасть"><input type="search" value={query} onChange={event => { setQuery(event.target.value); setOffset(0) }} /></FormField>
    <div aria-label="Фильтры" className="inventory-search-filters" role="group">
      <FormField id="inventory-component-filter" label="Компонента"><select value={componentId ?? ''} onChange={event => { setComponentId(event.target.value ? Number(event.target.value) : undefined); setOffset(0) }}><option value="">Все компоненты</option>{knownComponents.map(component => <option key={component.id} value={component.id}>{component.name}</option>)}</select></FormField>
      <FormField id="inventory-stock-filter" label="Остаток"><select value={stockFilter ?? ''} onChange={event => { setStockFilter(event.target.value as InventoryStockFilter || undefined); setOffset(0) }}><option value="">Все остатки</option><option value="in_stock">Есть на складе</option><option value="below_minimum">Ниже минимума</option><option value="without_location">Без места</option></select></FormField>
    </div>
    {failure ? <ErrorState description={failure.description} title={failure.title} /> : null}
    {loading && !result ? <LoadingState label="Ищем запчасти" /> : null}
    {!loading && result && !result.items.length ? <EmptyState description="Измените запрос или фильтры." icon="work" title="Запчасти не найдены" /> : null}
    <div className="inventory-search-results">{result?.items.map(item => {
      const selected = item.id === selectedId
      const low = inventoryInt64Compare(item.quantity, item.minimum_quantity) < 0
      return <article className={`inventory-search-card${selected ? ' inventory-search-card--selected' : ''}`} key={item.id}>
        {item.has_photo ? <img alt={item.name} className="inventory-search-card__photo" height={72} src={apiClient.inventoryPartPhotoUrl(-item.id)} width={72} /> : <div className="inventory-photo-placeholder inventory-search-card__photo">Нет фото</div>}
        <div className="inventory-search-card__body"><div className="inventory-search-card__heading"><div><h3>{item.name}</h3><p>{item.component_name} · <strong>{item.article}</strong></p></div><StatusBadge tone={item.quantity === '0' ? 'critical' : low ? 'warning' : 'success'}>{item.quantity} шт.</StatusBadge></div>
          <p className="inventory-search-card__location">{item.location || 'Место не указано'}</p>
          <div className="inventory-card-actions"><Button onClick={() => openWorkflow(item.id, 'stock')} size="compact" variant={selected && workflow?.kind === 'stock' ? 'primary' : 'secondary'}>Настроить остаток</Button><Button onClick={() => print(item)} size="compact" variant="ghost">Печатать этикетку</Button></div>
          {selected && workflow?.kind === 'stock' ? <StockForm apiClient={apiClient} onSaved={updateLocalStock} parkId={parkId} part={item} /> : null}
        </div>
      </article>
    })}</div>
    {result && result.total > result.limit ? <nav aria-label="Страницы запчастей" className="inventory-pagination"><Button disabled={offset === 0} onClick={() => setOffset(current => Math.max(0, current - 25))} size="compact" variant="secondary">Предыдущая страница</Button><span>{offset + 1}–{Math.min(offset + result.limit, result.total)} из {result.total}</span><Button disabled={offset + result.limit >= result.total} onClick={() => setOffset(current => current + 25)} size="compact" variant="secondary">Следующая страница</Button></nav> : null}
    <InventoryLabels parts={workflow?.kind === 'label' ? result?.items.filter(item => item.id === workflow.catalogPartId) ?? [] : []} />
  </section>
}
