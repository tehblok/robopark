import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { api, inventoryErrorDetail, isInventoryDuplicateErrorDetail, type InventoryCatalogSearchItem, type InventoryStockView } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { LoadingState } from '../../design-system/feedback/AsyncState'
import { FormField } from '../../design-system/forms/FormField'
import { ConfirmDialog } from '../../design-system/overlays/ConfirmDialog'
import { classifyApiError } from '../../shared/api/classifyApiError'
import { inventoryQuantityError, isInventoryQuantity } from './inventoryTypes'

type InventoryManageApi = Pick<typeof api,
  | 'searchInventory'
  | 'inventoryCatalogComponents'
  | 'getInventoryCatalogPart'
  | 'createInventoryCatalogComponent'
  | 'createInventoryCatalogPart'
  | 'updateInventoryCatalogPart'
  | 'mergeInventoryCatalogPart'
  | 'updateInventoryStock'
>
type ManageWorkflow = 'create' | 'component' | 'stock' | 'global-edit' | 'merge' | null
type Draft = { componentId: string; name: string; article: string; minimum: string; location: string }

export type InventoryManageViewProps = {
  apiClient?: InventoryManageApi
  parkId: number
  role?: string
  selectedCatalogPartId?: number | null
  onSelectedCatalogPartIdChange?: (catalogPartId: number | null) => void
}

const emptyDraft: Draft = { componentId: '', name: '', article: '', minimum: '0', location: '' }

function ParkStockForm({ apiClient, onSaved, parkId, part }: { apiClient: InventoryManageApi; onSaved: (stock: InventoryStockView, requestedParkId: number) => void; parkId: number; part: InventoryCatalogSearchItem }) {
  const [minimum, setMinimum] = useState<string>(part.minimum_quantity)
  const [location, setLocation] = useState(part.location ?? '')
  const [active, setActive] = useState(part.stock_is_active)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const generation = useRef(0)
  useEffect(() => () => { generation.current += 1 }, [])
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!isInventoryQuantity(minimum)) return
    setBusy(true)
    setError('')
    const requestId = ++generation.current
    const requestedParkId = parkId
    try {
      const stock = await apiClient.updateInventoryStock(requestedParkId, part.id, { minimum_quantity: minimum, location, is_active: active })
      if (requestId === generation.current && stock.park_id === requestedParkId) onSaved(stock, requestedParkId)
    } catch (reason) {
      setError(classifyApiError(reason, 'Не удалось сохранить настройки парка.').description)
    } finally {
      setBusy(false)
    }
  }
  return <form aria-label="Настройки остатка" className="inventory-manage-form" onSubmit={submit}>
    <h3>Остаток парка</h3>
    <FormField id={`manage-location-${part.id}`} label="Место"><input value={location} onChange={event => setLocation(event.target.value)} /></FormField>
    <FormField error={inventoryQuantityError(minimum)} id={`manage-minimum-${part.id}`} label="Минимум"><input inputMode="numeric" pattern="[0-9]*" value={minimum} onChange={event => setMinimum(event.target.value)} /></FormField>
    <label className="inventory-checkbox"><input checked={active} type="checkbox" onChange={event => setActive(event.target.checked)} /> Активна в парке</label>
    <Button busy={busy} disabled={!isInventoryQuantity(minimum)} type="submit">Сохранить</Button>
    {error ? <p className="form-error" role="alert">{error}</p> : null}
  </form>
}

export function InventoryManageView({ apiClient = api, parkId, role, selectedCatalogPartId, onSelectedCatalogPartIdChange }: InventoryManageViewProps) {
  const [items, setItems] = useState<InventoryCatalogSearchItem[]>([])
  const [components, setComponents] = useState<Array<{ id: number; name: string }>>([])
  const [catalogQuery, setCatalogQuery] = useState('')
  const [catalogOffset, setCatalogOffset] = useState(0)
  const [catalogTotal, setCatalogTotal] = useState(0)
  const [selectedPart, setSelectedPart] = useState<InventoryCatalogSearchItem | null>(null)
  const [archiveOpen, setArchiveOpen] = useState(false)
  const [internalSelectedId, setInternalSelectedId] = useState<number | null>(null)
  const [workflow, setWorkflow] = useState<ManageWorkflow>(null)
  const [draft, setDraft] = useState<Draft>(emptyDraft)
  const [componentName, setComponentName] = useState('')
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const generation = useRef(0)
  const activeParkId = useRef(parkId)
  const isSelectionControlled = selectedCatalogPartId !== undefined
  const selectedId = selectedCatalogPartId === undefined ? internalSelectedId : selectedCatalogPartId
  const canCreate = role === 'mechanic' || role === 'admin' || role === 'royal'
  const canManageGlobal = role === 'admin' || role === 'royal'
  const selected = selectedPart?.id === selectedId ? selectedPart : items.find(item => item.id === selectedId) ?? null
  const select = useCallback((id: number | null) => {
    if (!isSelectionControlled) setInternalSelectedId(id)
    onSelectedCatalogPartIdChange?.(id)
  }, [isSelectionControlled, onSelectedCatalogPartIdChange])
  const load = useCallback(async () => {
    const requestId = ++generation.current
    setLoading(true)
    setError('')
    try {
      const value = await apiClient.searchInventory({ parkId, query: catalogQuery.trim() || undefined, limit: 25, offset: catalogOffset })
      if (requestId !== generation.current) return
      setItems(value.items)
      setCatalogTotal(value.total)
      setSelectedPart(current => value.items.find(item => item.id === current?.id) ?? current)
      setLoading(false)
    } catch (reason) {
      if (requestId !== generation.current) return
      setError(classifyApiError(reason, 'Не удалось загрузить каталог.').description)
      setLoading(false)
    }
  }, [apiClient, catalogOffset, catalogQuery, parkId])

  useEffect(() => {
    setItems([])
    activeParkId.current = parkId
    setWorkflow(null)
    setSelectedPart(null)
    setCatalogOffset(0)
    select(null)
    apiClient.inventoryCatalogComponents(parkId, { limit: 200, offset: 0 }).then(value => {
      if (activeParkId.current === parkId) setComponents(value.items.map(item => ({ id: item.id, name: item.name })))
    }).catch(() => {})
    return () => { generation.current += 1 }
  }, [apiClient, parkId, select])

  useEffect(() => {
    const timer = globalThis.setTimeout(() => { void load() }, catalogQuery ? 200 : 0)
    return () => { globalThis.clearTimeout(timer); generation.current += 1 }
  }, [catalogQuery, load])

  const updateStockItem = (stock: InventoryStockView, requestedParkId: number) => {
    if (activeParkId.current !== requestedParkId || stock.park_id !== requestedParkId) return
    setItems(current => current.map(item => item.id === stock.catalog_part_id ? {
    ...item,
    minimum_quantity: stock.minimum_quantity,
    location: stock.location,
    stock_is_active: stock.is_active,
    } : item))
  }
  const chooseWorkflow = (next: ManageWorkflow) => { setError(''); setNotice(''); setWorkflow(next) }
  const createPart = async (event: FormEvent) => {
    event.preventDefault()
    const componentId = Number(draft.componentId)
    if (!componentId || !draft.name.trim() || !draft.article.trim() || !isInventoryQuantity(draft.minimum)) return
    setBusy(true)
    setError('')
    setNotice('')
    const requestedParkId = parkId
    try {
      const created = await apiClient.createInventoryCatalogPart({ park_id: requestedParkId, component_id: componentId, name: draft.name, article: draft.article })
      const stock = await apiClient.updateInventoryStock(requestedParkId, created.id, { minimum_quantity: draft.minimum, location: draft.location, is_active: true })
      if (activeParkId.current !== requestedParkId || stock.park_id !== requestedParkId) return
      const component = components.find(item => item.id === componentId)
      setItems(current => [...current.filter(item => item.id !== created.id), { ...created, component_name: component?.name ?? '', quantity: stock.quantity, minimum_quantity: stock.minimum_quantity, location: stock.location, stock_is_active: stock.is_active }])
      select(created.id)
      setDraft(emptyDraft)
      setWorkflow('stock')
      setNotice('Позиция создана и добавлена в склад парка.')
    } catch (reason) {
      const detail = inventoryErrorDetail(reason)
      if (isInventoryDuplicateErrorDetail(detail) && detail.code === 'inventory_article_exists') {
        if (!items.some(item => item.id === detail.existing_part_id)) {
          try {
            const existing = await apiClient.getInventoryCatalogPart(parkId, detail.existing_part_id)
            setItems(current => [...current.filter(item => item.id !== existing.id), existing])
            setSelectedPart(existing)
          } catch {
            setError('Не удалось открыть существующую позицию.')
            return
          }
        }
        select(detail.existing_part_id)
        setWorkflow('stock')
        setNotice('Позиция с этим артикулом уже есть. Открыты её настройки для парка.')
      } else {
        setError(classifyApiError(reason, 'Не удалось создать позицию.').description)
      }
    } finally {
      setBusy(false)
    }
  }

  return <section className="inventory-manage-view">
    <header className="inventory-manage-heading"><h2>{canManageGlobal ? 'Глобальный каталог' : 'Настройки склада парка'}</h2><p>{canManageGlobal ? 'Глобальные позиции и настройки склада выбранного парка.' : 'Параметры остатков выбранного парка.'}</p></header>
    <div className="inventory-manage-toolbar">
      <FormField id="inventory-manage-search" label="Найти позицию каталога"><input type="search" value={catalogQuery} onChange={event => { setCatalogQuery(event.target.value); setCatalogOffset(0) }} /></FormField>
      <FormField id="inventory-manage-part" label="Позиция каталога"><select value={selectedId ?? ''} onChange={event => { const id = event.target.value ? Number(event.target.value) : null; select(id); setSelectedPart(items.find(item => item.id === id) ?? null); setWorkflow(null) }}><option value="">Выберите</option>{selected && !items.some(item => item.id === selected.id) ? <option value={selected.id}>{selected.name} · {selected.article}</option> : null}{items.map(item => <option key={item.id} value={item.id}>{item.name} · {item.article}</option>)}</select></FormField>
      <div className="inventory-card-actions">
        {canCreate ? <><Button onClick={() => chooseWorkflow('create')} size="compact">Добавить позицию</Button><Button onClick={() => chooseWorkflow('component')} size="compact" variant="secondary">Добавить компоненту</Button></> : null}
        {selected ? <Button onClick={() => chooseWorkflow('stock')} size="compact" variant="secondary">Настроить остаток</Button> : null}
        {canManageGlobal && selected ? <><Button onClick={() => chooseWorkflow('global-edit')} size="compact" variant="secondary">Редактировать глобально</Button><Button onClick={() => setArchiveOpen(true)} size="compact" variant="danger">Архивировать глобально</Button><Button onClick={() => chooseWorkflow('merge')} size="compact" variant="danger">Объединить глобально</Button></> : null}
      </div>
    </div>
    {loading ? <LoadingState label="Загружаем каталог" /> : null}
    {catalogTotal > 25 ? <nav aria-label="Страницы каталога" className="inventory-pagination"><Button disabled={catalogOffset === 0} onClick={() => setCatalogOffset(value => Math.max(0, value - 25))} size="compact" variant="secondary">Предыдущая страница каталога</Button><Button disabled={catalogOffset + 25 >= catalogTotal} onClick={() => setCatalogOffset(value => value + 25)} size="compact" variant="secondary">Следующая страница каталога</Button></nav> : null}
    {notice ? <p className="inventory-notice" role="status">{notice}</p> : null}
    {error ? <p className="form-error" role="alert">{error}</p> : null}
    {workflow === 'component' ? <form aria-label="Новая компонента" className="inventory-manage-form" onSubmit={async event => { event.preventDefault(); setBusy(true); setError(''); try { const created = await apiClient.createInventoryCatalogComponent({ park_id: parkId, name: componentName }); setDraft(current => ({ ...current, componentId: String(created.id) })); setComponentName(''); setWorkflow('create'); await load() } catch (reason) { setError(classifyApiError(reason, 'Не удалось создать компоненту.').description) } finally { setBusy(false) } }}><h3>Новая компонента</h3><FormField id="inventory-new-component" label="Название" required><input value={componentName} onChange={event => setComponentName(event.target.value)} /></FormField><Button busy={busy} disabled={!componentName.trim()} type="submit">Создать</Button></form> : null}
    {workflow === 'create' ? <form aria-label="Новая позиция" className="inventory-manage-form" onSubmit={createPart}><h3>Новая позиция</h3><FormField id="inventory-new-part-component" label="Компонента" required><select value={draft.componentId} onChange={event => setDraft(current => ({ ...current, componentId: event.target.value }))}><option value="">Выберите</option>{components.map(component => <option key={component.id} value={component.id}>{component.name}</option>)}</select></FormField><FormField id="inventory-new-part-name" label="Название" required><input value={draft.name} onChange={event => setDraft(current => ({ ...current, name: event.target.value }))} /></FormField><FormField id="inventory-new-part-article" label="Артикул" required><input value={draft.article} onChange={event => setDraft(current => ({ ...current, article: event.target.value }))} /></FormField><FormField id="inventory-new-part-location" label="Место"><input value={draft.location} onChange={event => setDraft(current => ({ ...current, location: event.target.value }))} /></FormField><FormField error={inventoryQuantityError(draft.minimum)} id="inventory-new-part-minimum" label="Минимум"><input inputMode="numeric" pattern="[0-9]*" value={draft.minimum} onChange={event => setDraft(current => ({ ...current, minimum: event.target.value }))} /></FormField><Button busy={busy} disabled={!draft.componentId || !draft.name.trim() || !draft.article.trim() || !isInventoryQuantity(draft.minimum)} type="submit">Создать</Button></form> : null}
    {workflow === 'stock' && selected ? <ParkStockForm apiClient={apiClient} onSaved={updateStockItem} parkId={parkId} part={selected} /> : null}
    {workflow === 'global-edit' && selected && canManageGlobal ? <GlobalPartForm apiClient={apiClient} onSaved={async () => { await load(); setWorkflow(null) }} part={selected} /> : null}
    {workflow === 'merge' && selected && canManageGlobal ? <MergePartForm apiClient={apiClient} onSaved={async targetId => { select(targetId); await load(); setWorkflow(null) }} parkId={parkId} source={selected} /> : null}
    <ConfirmDialog confirmLabel="Подтвердить архивирование" description={selected ? `Позиция «${selected.name}» исчезнет из активного каталога.` : ''} onConfirm={async () => { if (!selected) return; setBusy(true); setError(''); try { await apiClient.updateInventoryCatalogPart(selected.id, { is_active: false }); setArchiveOpen(false); select(null); setSelectedPart(null); await load() } catch (reason) { setError(classifyApiError(reason, 'Не удалось архивировать позицию.').description) } finally { setBusy(false) } }} onOpenChange={setArchiveOpen} open={archiveOpen} pending={busy} title="Архивировать глобальную позицию?" tone="danger" />
  </section>
}

function GlobalPartForm({ apiClient, onSaved, part }: { apiClient: InventoryManageApi; onSaved: () => void | Promise<void>; part: InventoryCatalogSearchItem }) {
  const [name, setName] = useState(part.name)
  const [article, setArticle] = useState(part.article)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  return <form aria-label="Глобальная позиция" className="inventory-manage-form" onSubmit={async event => { event.preventDefault(); setBusy(true); setError(''); try { await apiClient.updateInventoryCatalogPart(part.id, { name, article }); await onSaved() } catch (reason) { setError(classifyApiError(reason, 'Не удалось изменить глобальную позицию.').description) } finally { setBusy(false) } }}><h3>Глобальная позиция</h3><FormField id={`global-name-${part.id}`} label="Название" required><input value={name} onChange={event => setName(event.target.value)} /></FormField><FormField id={`global-article-${part.id}`} label="Артикул" required><input value={article} onChange={event => setArticle(event.target.value)} /></FormField><Button busy={busy} type="submit">Сохранить</Button>{error ? <p className="form-error" role="alert">{error}</p> : null}</form>
}

function MergePartForm({ apiClient, onSaved, parkId, source }: { apiClient: InventoryManageApi; onSaved: (targetId: number) => void | Promise<void>; parkId: number; source: InventoryCatalogSearchItem }) {
  const [targetId, setTargetId] = useState('')
  const [query, setQuery] = useState('')
  const [offset, setOffset] = useState(0)
  const [targets, setTargets] = useState<InventoryCatalogSearchItem[]>([])
  const [total, setTotal] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    const timer = globalThis.setTimeout(() => {
      apiClient.searchInventory({ parkId, query: query.trim() || undefined, limit: 25, offset }).then(value => { setTargets(value.items.filter(item => item.id !== source.id)); setTotal(value.total) }).catch(reason => setError(classifyApiError(reason, 'Не удалось загрузить целевые позиции.').description))
    }, query ? 200 : 0)
    return () => globalThis.clearTimeout(timer)
  }, [apiClient, offset, parkId, query, source.id])
  return <form aria-label="Объединение позиций" className="inventory-manage-form" onSubmit={async event => { event.preventDefault(); if (!targetId) return; setBusy(true); setError(''); try { await apiClient.mergeInventoryCatalogPart(source.id, Number(targetId)); await onSaved(Number(targetId)) } catch (reason) { setError(classifyApiError(reason, 'Не удалось объединить позиции.').description) } finally { setBusy(false) } }}><h3>Объединить «{source.name}»</h3><FormField id={`merge-search-${source.id}`} label="Найти целевую позицию"><input type="search" value={query} onChange={event => { setQuery(event.target.value); setOffset(0) }} /></FormField><FormField hint="Остатки и история перейдут в целевую позицию." id={`merge-target-${source.id}`} label="Целевая позиция" required><select value={targetId} onChange={event => setTargetId(event.target.value)}><option value="">Выберите</option>{targets.map(item => <option key={item.id} value={item.id}>{item.name} · {item.article}</option>)}</select></FormField>{total > 25 ? <div className="inventory-pagination"><Button disabled={offset === 0} onClick={() => setOffset(value => Math.max(0, value - 25))} size="compact" type="button" variant="secondary">Назад</Button><Button disabled={offset + 25 >= total} onClick={() => setOffset(value => value + 25)} size="compact" type="button" variant="secondary">Дальше</Button></div> : null}<Button busy={busy} disabled={!targetId} type="submit" variant="danger">Объединить</Button>{error ? <p className="form-error" role="alert">{error}</p> : null}</form>
}
