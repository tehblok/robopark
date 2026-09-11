import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, inventoryErrorDetail, isInventoryDuplicateErrorDetail, type InventoryCatalogSearchItem, type InventoryStockView } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { LoadingState } from '../../design-system/feedback/AsyncState'
import { FormField } from '../../design-system/forms/FormField'
import { classifyApiError } from '../../shared/api/classifyApiError'
import { isInventoryQuantity } from './inventoryTypes'

type InventoryManageApi = Pick<typeof api,
  | 'searchInventory'
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

function ParkStockForm({ apiClient, onSaved, parkId, part }: { apiClient: InventoryManageApi; onSaved: (stock: InventoryStockView) => void; parkId: number; part: InventoryCatalogSearchItem }) {
  const [minimum, setMinimum] = useState<string>(part.minimum_quantity)
  const [location, setLocation] = useState(part.location ?? '')
  const [active, setActive] = useState(part.stock_is_active)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!isInventoryQuantity(minimum)) return
    setBusy(true)
    setError('')
    try {
      onSaved(await apiClient.updateInventoryStock(parkId, part.id, { minimum_quantity: minimum, location, is_active: active }))
    } catch (reason) {
      setError(classifyApiError(reason, 'Не удалось сохранить настройки парка.').description)
    } finally {
      setBusy(false)
    }
  }
  return <form aria-label="Настройки остатка" className="inventory-manage-form" onSubmit={submit}>
    <h3>Остаток парка</h3>
    <FormField id={`manage-location-${part.id}`} label="Место"><input value={location} onChange={event => setLocation(event.target.value)} /></FormField>
    <FormField error={!isInventoryQuantity(minimum) ? 'Целое неотрицательное число' : undefined} id={`manage-minimum-${part.id}`} label="Минимум"><input inputMode="numeric" pattern="[0-9]*" value={minimum} onChange={event => setMinimum(event.target.value)} /></FormField>
    <label className="inventory-checkbox"><input checked={active} type="checkbox" onChange={event => setActive(event.target.checked)} /> Активна в парке</label>
    <Button busy={busy} disabled={!isInventoryQuantity(minimum)} type="submit">Сохранить</Button>
    {error ? <p className="form-error" role="alert">{error}</p> : null}
  </form>
}

export function InventoryManageView({ apiClient = api, parkId, role, selectedCatalogPartId, onSelectedCatalogPartIdChange }: InventoryManageViewProps) {
  const [items, setItems] = useState<InventoryCatalogSearchItem[]>([])
  const [internalSelectedId, setInternalSelectedId] = useState<number | null>(null)
  const [workflow, setWorkflow] = useState<ManageWorkflow>(null)
  const [draft, setDraft] = useState<Draft>(emptyDraft)
  const [componentName, setComponentName] = useState('')
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const generation = useRef(0)
  const isSelectionControlled = selectedCatalogPartId !== undefined
  const selectedId = selectedCatalogPartId === undefined ? internalSelectedId : selectedCatalogPartId
  const canCreate = role === 'mechanic' || role === 'admin' || role === 'royal'
  const canManageGlobal = role === 'admin' || role === 'royal'
  const selected = items.find(item => item.id === selectedId) ?? null
  const components = useMemo(() => {
    const byId = new Map<number, string>()
    items.forEach(item => byId.set(item.component_id, item.component_name))
    return [...byId].map(([id, name]) => ({ id, name })).sort((left, right) => left.name.localeCompare(right.name, 'ru'))
  }, [items])
  const select = useCallback((id: number | null) => {
    if (!isSelectionControlled) setInternalSelectedId(id)
    onSelectedCatalogPartIdChange?.(id)
  }, [isSelectionControlled, onSelectedCatalogPartIdChange])
  const load = useCallback(async () => {
    const requestId = ++generation.current
    setLoading(true)
    setError('')
    try {
      const value = await apiClient.searchInventory({ parkId, limit: 200, offset: 0 })
      if (requestId !== generation.current) return
      setItems(value.items)
      setLoading(false)
    } catch (reason) {
      if (requestId !== generation.current) return
      setError(classifyApiError(reason, 'Не удалось загрузить каталог.').description)
      setLoading(false)
    }
  }, [apiClient, parkId])

  useEffect(() => {
    setItems([])
    setWorkflow(null)
    select(null)
    void load()
    return () => { generation.current += 1 }
  }, [load, parkId, select])

  const updateStockItem = (stock: InventoryStockView) => setItems(current => current.map(item => item.id === stock.catalog_part_id ? {
    ...item,
    minimum_quantity: stock.minimum_quantity,
    location: stock.location,
    stock_is_active: stock.is_active,
  } : item))
  const chooseWorkflow = (next: ManageWorkflow) => { setError(''); setNotice(''); setWorkflow(next) }
  const createPart = async (event: FormEvent) => {
    event.preventDefault()
    const componentId = Number(draft.componentId)
    if (!componentId || !draft.name.trim() || !draft.article.trim() || !isInventoryQuantity(draft.minimum)) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const created = await apiClient.createInventoryCatalogPart({ park_id: parkId, component_id: componentId, name: draft.name, article: draft.article })
      const stock = await apiClient.updateInventoryStock(parkId, created.id, { minimum_quantity: draft.minimum, location: draft.location, is_active: true })
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
            const duplicate = await apiClient.searchInventory({ parkId, query: draft.article.trim(), limit: 25, offset: 0 })
            const existing = duplicate.items.find(item => item.id === detail.existing_part_id)
            if (existing) setItems(current => [...current.filter(item => item.id !== existing.id), existing])
          } catch {
            // Keep the duplicate selection even if the supplemental lookup fails.
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
    <div className="inventory-manage-toolbar">
      <FormField id="inventory-manage-part" label="Позиция каталога"><select value={selectedId ?? ''} onChange={event => { select(event.target.value ? Number(event.target.value) : null); setWorkflow(null) }}><option value="">Выберите</option>{items.map(item => <option key={item.id} value={item.id}>{item.name} · {item.article}</option>)}</select></FormField>
      <div className="inventory-card-actions">
        {canCreate ? <><Button onClick={() => chooseWorkflow('create')} size="compact">Добавить позицию</Button><Button onClick={() => chooseWorkflow('component')} size="compact" variant="secondary">Добавить компоненту</Button></> : null}
        {selected ? <Button onClick={() => chooseWorkflow('stock')} size="compact" variant="secondary">Настроить остаток</Button> : null}
        {canManageGlobal && selected ? <><Button onClick={() => chooseWorkflow('global-edit')} size="compact" variant="secondary">Редактировать глобально</Button><Button onClick={async () => { setError(''); try { await apiClient.updateInventoryCatalogPart(selected.id, { is_active: false }); await load() } catch (reason) { setError(classifyApiError(reason, 'Не удалось архивировать позицию.').description) } }} size="compact" variant="danger">Архивировать глобально</Button><Button onClick={() => chooseWorkflow('merge')} size="compact" variant="danger">Объединить глобально</Button></> : null}
      </div>
    </div>
    {loading ? <LoadingState label="Загружаем каталог" /> : null}
    {notice ? <p className="inventory-notice" role="status">{notice}</p> : null}
    {error ? <p className="form-error" role="alert">{error}</p> : null}
    {workflow === 'component' ? <form aria-label="Новая компонента" className="inventory-manage-form" onSubmit={async event => { event.preventDefault(); setBusy(true); setError(''); try { const created = await apiClient.createInventoryCatalogComponent({ park_id: parkId, name: componentName }); setDraft(current => ({ ...current, componentId: String(created.id) })); setComponentName(''); setWorkflow('create'); await load() } catch (reason) { setError(classifyApiError(reason, 'Не удалось создать компоненту.').description) } finally { setBusy(false) } }}><h3>Новая компонента</h3><FormField id="inventory-new-component" label="Название" required><input value={componentName} onChange={event => setComponentName(event.target.value)} /></FormField><Button busy={busy} disabled={!componentName.trim()} type="submit">Создать</Button></form> : null}
    {workflow === 'create' ? <form aria-label="Новая позиция" className="inventory-manage-form" onSubmit={createPart}><h3>Новая позиция</h3><FormField id="inventory-new-part-component" label="Компонента" required><select value={draft.componentId} onChange={event => setDraft(current => ({ ...current, componentId: event.target.value }))}><option value="">Выберите</option>{components.map(component => <option key={component.id} value={component.id}>{component.name}</option>)}</select></FormField><FormField id="inventory-new-part-name" label="Название" required><input value={draft.name} onChange={event => setDraft(current => ({ ...current, name: event.target.value }))} /></FormField><FormField id="inventory-new-part-article" label="Артикул" required><input value={draft.article} onChange={event => setDraft(current => ({ ...current, article: event.target.value }))} /></FormField><FormField id="inventory-new-part-location" label="Место"><input value={draft.location} onChange={event => setDraft(current => ({ ...current, location: event.target.value }))} /></FormField><FormField error={!isInventoryQuantity(draft.minimum) ? 'Целое неотрицательное число' : undefined} id="inventory-new-part-minimum" label="Минимум"><input inputMode="numeric" pattern="[0-9]*" value={draft.minimum} onChange={event => setDraft(current => ({ ...current, minimum: event.target.value }))} /></FormField><Button busy={busy} disabled={!draft.componentId || !draft.name.trim() || !draft.article.trim() || !isInventoryQuantity(draft.minimum)} type="submit">Создать</Button></form> : null}
    {workflow === 'stock' && selected ? <ParkStockForm apiClient={apiClient} onSaved={updateStockItem} parkId={parkId} part={selected} /> : null}
    {workflow === 'global-edit' && selected && canManageGlobal ? <GlobalPartForm apiClient={apiClient} onSaved={async () => { await load(); setWorkflow(null) }} part={selected} /> : null}
    {workflow === 'merge' && selected && canManageGlobal ? <MergePartForm apiClient={apiClient} items={items} onSaved={async targetId => { select(targetId); await load(); setWorkflow(null) }} source={selected} /> : null}
  </section>
}

function GlobalPartForm({ apiClient, onSaved, part }: { apiClient: InventoryManageApi; onSaved: () => void | Promise<void>; part: InventoryCatalogSearchItem }) {
  const [name, setName] = useState(part.name)
  const [article, setArticle] = useState(part.article)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  return <form aria-label="Глобальная позиция" className="inventory-manage-form" onSubmit={async event => { event.preventDefault(); setBusy(true); setError(''); try { await apiClient.updateInventoryCatalogPart(part.id, { name, article }); await onSaved() } catch (reason) { setError(classifyApiError(reason, 'Не удалось изменить глобальную позицию.').description) } finally { setBusy(false) } }}><h3>Глобальная позиция</h3><FormField id={`global-name-${part.id}`} label="Название" required><input value={name} onChange={event => setName(event.target.value)} /></FormField><FormField id={`global-article-${part.id}`} label="Артикул" required><input value={article} onChange={event => setArticle(event.target.value)} /></FormField><Button busy={busy} type="submit">Сохранить</Button>{error ? <p className="form-error" role="alert">{error}</p> : null}</form>
}

function MergePartForm({ apiClient, items, onSaved, source }: { apiClient: InventoryManageApi; items: InventoryCatalogSearchItem[]; onSaved: (targetId: number) => void | Promise<void>; source: InventoryCatalogSearchItem }) {
  const [targetId, setTargetId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  return <form aria-label="Объединение позиций" className="inventory-manage-form" onSubmit={async event => { event.preventDefault(); if (!targetId) return; setBusy(true); setError(''); try { await apiClient.mergeInventoryCatalogPart(source.id, Number(targetId)); await onSaved(Number(targetId)) } catch (reason) { setError(classifyApiError(reason, 'Не удалось объединить позиции.').description) } finally { setBusy(false) } }}><h3>Объединить «{source.name}»</h3><FormField hint="Остатки и история перейдут в целевую позицию." id={`merge-target-${source.id}`} label="Целевая позиция" required><select value={targetId} onChange={event => setTargetId(event.target.value)}><option value="">Выберите</option>{items.filter(item => item.id !== source.id).map(item => <option key={item.id} value={item.id}>{item.name} · {item.article}</option>)}</select></FormField><Button busy={busy} disabled={!targetId} type="submit" variant="danger">Объединить</Button>{error ? <p className="form-error" role="alert">{error}</p> : null}</form>
}
