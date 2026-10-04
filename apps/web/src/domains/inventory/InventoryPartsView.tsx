import { type FormEvent, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { api, type InventoryCatalogSearchItem, type InventoryPageEnvelope, type InventorySearchParams, type InventoryStockFilter, type InventoryStockView } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { FormField } from '../../design-system/forms/FormField'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { classifyApiError } from '../../shared/api/classifyApiError'
import { InventoryLabels } from './InventoryLabels'
import { retainInventoryPartRecords } from './inventoryPartRetention'
import { inventoryInt64Compare, inventoryQuantityError, isInventoryQuantity } from './inventoryTypes'
import { INVENTORY_COMPONENTS_INCOMPLETE, loadInventoryComponents } from './loadInventoryComponents'

type InventoryPartsApi = Pick<typeof api, 'searchInventory' | 'inventoryCatalogComponents' | 'updateInventoryStock' | 'inventoryPartPhotoUrl'>
type PartWorkflowKind = 'stock'
type PartWorkflow = { catalogPartId: number; kind: PartWorkflowKind } | null

export type InventoryPartsViewProps = {
  apiClient?: InventoryPartsApi
  canManage?: boolean
  canPrint?: boolean
  debounceMs?: number
  parkId: number
  refreshVersion?: number
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

export function InventoryPartsView({ apiClient = api, canManage = true, canPrint = true, debounceMs = 300, parkId, refreshVersion = 0, selectedCatalogPartId, onSelectedCatalogPartIdChange }: InventoryPartsViewProps) {
  const [query, setQuery] = useState('')
  const [componentId, setComponentId] = useState<number | undefined>()
  const [stockFilter, setStockFilter] = useState<InventoryStockFilter | undefined>()
  const [offset, setOffset] = useState(0)
  const [result, setResult] = useState<InventoryPageEnvelope<InventoryCatalogSearchItem> | null>(null)
  const [knownComponents, setKnownComponents] = useState<Array<{ id: number; name: string }>>([])
  const [error, setError] = useState<unknown>(null)
  const [componentError, setComponentError] = useState('')
  const [loading, setLoading] = useState(true)
  const [internalSelectedId, setInternalSelectedId] = useState<number | null>(null)
  const [workflow, setWorkflow] = useState<PartWorkflow>(null)
  const [selectedLabelIds, setSelectedLabelIds] = useState<Set<number>>(() => new Set())
  const [labelsToPrint, setLabelsToPrint] = useState<InventoryCatalogSearchItem[]>([])
  const partsById = useRef(new Map<number, InventoryCatalogSearchItem>())
  const generation = useRef(0)
  const accessGeneration = useRef(0)
  const activeParkId = useRef(parkId)
  useEffect(() => { activeParkId.current = parkId }, [parkId])
  const selectedId = selectedCatalogPartId === undefined ? internalSelectedId : selectedCatalogPartId
  const selection = useRef({ controlled: selectedCatalogPartId !== undefined, onChange: onSelectedCatalogPartIdChange })
  useLayoutEffect(() => {
    selection.current = { controlled: selectedCatalogPartId !== undefined, onChange: onSelectedCatalogPartIdChange }
  }, [onSelectedCatalogPartIdChange, selectedCatalogPartId])
  useEffect(() => {
    partsById.current = retainInventoryPartRecords(result?.items ?? [], selectedLabelIds, partsById.current)
  }, [result, selectedLabelIds])
  const select = useCallback((id: number | null) => {
    if (!selection.current.controlled) setInternalSelectedId(id)
    selection.current.onChange?.(id)
  }, [])
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
        const kind = classifyApiError(reason, '').kind
        if (kind === 'unauthorized' || kind === 'forbidden') {
          generation.current += 1
          accessGeneration.current += 1
          setResult(null)
          partsById.current.clear()
          setKnownComponents([])
          setSelectedLabelIds(new Set())
          setLabelsToPrint([])
          setWorkflow(null)
          select(null)
        }
        setError(reason)
        setLoading(false)
      })
    }, query ? debounceMs : 0)
    return () => { globalThis.clearTimeout(timer); generation.current += 1 }
  }, [apiClient, debounceMs, params, query, refreshVersion, select])

  useEffect(() => {
    const requestedParkId = parkId
    const requestGeneration = accessGeneration.current
    let active = true
    setComponentError('')
    loadInventoryComponents(apiClient, parkId).then(value => {
      if (active && activeParkId.current === requestedParkId && accessGeneration.current === requestGeneration) setKnownComponents(value.map(item => ({ id: item.id, name: item.name })))
    }).catch(() => { if (active && activeParkId.current === requestedParkId && accessGeneration.current === requestGeneration) setComponentError(INVENTORY_COMPONENTS_INCOMPLETE) })
    return () => { active = false }
  }, [apiClient, parkId, refreshVersion])

  useEffect(() => {
    setOffset(0)
    setResult(null)
    setKnownComponents([])
    setSelectedLabelIds(new Set())
    setLabelsToPrint([])
    partsById.current.clear()
    setWorkflow(null)
    select(null)
  // A park change starts a fresh, unselected catalog.
  }, [parkId, select])

  const failure = error ? classifyApiError(error, 'Не удалось загрузить запчасти.') : null
  const updateLocalStock = (stock: InventoryStockView, requestedParkId: number) => {
    if (requestedParkId !== activeParkId.current || stock.park_id !== requestedParkId) return
    setResult(current => {
      if (!current) return current
      const items = current.items.map(item => item.id === stock.catalog_part_id ? { ...item, minimum_quantity: stock.minimum_quantity, location: stock.location, stock_is_active: stock.is_active } : item)
      return { ...current, items }
    })
  }
  const openWorkflow = (catalogPartId: number, kind: PartWorkflowKind) => {
    select(catalogPartId)
    setWorkflow({ catalogPartId, kind })
  }
  const print = (ids: number[]) => {
    setWorkflow(null)
    setLabelsToPrint(ids.map(id => partsById.current.get(id)).filter((item): item is InventoryCatalogSearchItem => Boolean(item)))
    globalThis.setTimeout(() => window.print(), 0)
  }
  const togglePrintPart = (id: number) => setSelectedLabelIds(current => {
    const next = new Set(current)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })
  const chooseCurrentPage = () => setSelectedLabelIds(current => {
    const next = new Set(current)
    result?.items.forEach(item => next.add(item.id))
    return next
  })

  return <section className="inventory-catalog-view">
    <FormField id="inventory-part-search" label="Найти запчасть"><input type="search" value={query} onChange={event => { setQuery(event.target.value); setOffset(0) }} /></FormField>
    <div aria-label="Фильтры" className="inventory-search-filters" role="group">
      <FormField id="inventory-component-filter" label="Компонента"><select value={componentId ?? ''} onChange={event => { setComponentId(event.target.value ? Number(event.target.value) : undefined); setOffset(0) }}><option value="">Все компоненты</option>{knownComponents.map(component => <option key={component.id} value={component.id}>{component.name}</option>)}</select></FormField>
      <FormField id="inventory-stock-filter" label="Остаток"><select value={stockFilter ?? ''} onChange={event => { setStockFilter(event.target.value as InventoryStockFilter || undefined); setOffset(0) }}><option value="">Все остатки</option><option value="in_stock">Есть на складе</option><option value="below_minimum">Ниже минимума</option><option value="without_location">Без места</option></select></FormField>
    </div>
    {canPrint ? <div aria-label="Печать этикеток" className="inventory-label-actions" role="group"><Button disabled={!result?.items.length} onClick={chooseCurrentPage} size="compact" variant="secondary">Выбрать текущую страницу</Button><Button disabled={!selectedLabelIds.size} onClick={() => print([...selectedLabelIds])} size="compact">Печатать выбранные ({selectedLabelIds.size})</Button>{selectedLabelIds.size ? <Button onClick={() => { setSelectedLabelIds(new Set()); setLabelsToPrint([]) }} size="compact" variant="ghost">Очистить выбор</Button> : null}</div> : null}
    {componentError ? <p className="form-error" role="alert">{componentError}</p> : null}
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
          {canPrint ? <label className="inventory-label-choice"><input aria-label={`Выбрать для печати ${item.name}`} checked={selectedLabelIds.has(item.id)} onChange={() => togglePrintPart(item.id)} type="checkbox" /> В печать</label> : null}
          <div className="inventory-card-actions">{canManage ? <Button onClick={() => openWorkflow(item.id, 'stock')} size="compact" variant={selected && workflow?.kind === 'stock' ? 'primary' : 'secondary'}>Настроить остаток</Button> : null}{canPrint ? <Button onClick={() => { select(item.id); print([item.id]) }} size="compact" variant="ghost">Печатать этикетку</Button> : null}</div>
          {canManage && selected && workflow?.kind === 'stock' ? <StockForm apiClient={apiClient} onSaved={updateLocalStock} parkId={parkId} part={item} /> : null}
        </div>
      </article>
    })}</div>
    {result && result.total > result.limit ? <nav aria-label="Страницы запчастей" className="inventory-pagination"><Button disabled={offset === 0} onClick={() => setOffset(current => Math.max(0, current - 25))} size="compact" variant="secondary">Предыдущая страница</Button><span>{offset + 1}–{Math.min(offset + result.limit, result.total)} из {result.total}</span><Button disabled={offset + result.limit >= result.total} onClick={() => setOffset(current => current + 25)} size="compact" variant="secondary">Следующая страница</Button></nav> : null}
    {canPrint ? <InventoryLabels parts={labelsToPrint} /> : null}
  </section>
}
