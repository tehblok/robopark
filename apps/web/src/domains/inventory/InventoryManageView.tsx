import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { api, inventoryErrorDetail, isInventoryDuplicateErrorDetail, type InventoryCatalogDeleteSummary, type InventoryCatalogSearchItem, type InventoryStockView } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { LoadingState } from '../../design-system/feedback/AsyncState'
import { FormField } from '../../design-system/forms/FormField'
import { ResponsiveDisclosure, ResponsiveDisclosureGroup } from '../../design-system/layout/ResponsiveDisclosure'
import { ConfirmDialog } from '../../design-system/overlays/ConfirmDialog'
import { classifyApiError } from '../../shared/api/classifyApiError'
import { inventoryQuantityError, isInventoryQuantity } from './inventoryTypes'
import { INVENTORY_COMPONENTS_INCOMPLETE, loadInventoryComponents } from './loadInventoryComponents'

type InventoryManageApi = Pick<typeof api,
  | 'searchInventory'
  | 'inventoryCatalogComponents'
  | 'getInventoryCatalogPart'
  | 'createInventoryCatalogComponent'
  | 'createInventoryCatalogPart'
  | 'updateInventoryCatalogPart'
  | 'replaceInventoryCatalogComponentPhoto'
  | 'removeInventoryCatalogComponentPhoto'
  | 'replaceInventoryCatalogPartPhoto'
  | 'removeInventoryCatalogPartPhoto'
  | 'permanentlyDeleteInventoryCatalogComponent'
  | 'permanentlyDeleteInventoryCatalogPart'
  | 'mergeInventoryCatalogPart'
  | 'updateInventoryStock'
>
type ManageWorkflow = 'create' | 'component' | 'component-edit' | 'component-delete' | 'stock' | 'global-edit' | 'merge' | null
type Draft = { componentId: string; name: string; article: string; minimum: string; location: string }
type ManageComponent = { id: number; name: string; has_photo: boolean }

export type InventoryManageViewProps = {
  apiClient?: InventoryManageApi
  parkId: number
  refreshVersion?: number
  role?: string
  selectedCatalogPartId?: number | null
  onSelectedCatalogPartIdChange?: (catalogPartId: number | null) => void
}

const emptyDraft: Draft = { componentId: '', name: '', article: '', minimum: '0', location: '' }

function PhotoFileAction({ hasPhoto, id, onChange }: { hasPhoto: boolean; id: string; onChange: (file: File | undefined) => void }) {
  const input = useRef<HTMLInputElement>(null)
  const label = hasPhoto ? 'Заменить' : 'Сделать фото или выбрать файл'
  return <div className="rp-action-bar"><Button onClick={() => input.current?.click()} type="button" variant="secondary">{label}</Button><input accept="image/jpeg,image/png,image/webp" aria-label={label} capture="environment" className="issue-attach-input" id={id} onChange={event => onChange(event.target.files?.[0])} ref={input} type="file" /></div>
}

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
  return <form aria-label="Настройки остатка" className="inventory-manage-form rp-form-stack--mobile" onSubmit={submit}>
    <h3>Остаток парка</h3>
    <FormField id={`manage-location-${part.id}`} label="Место"><input value={location} onChange={event => setLocation(event.target.value)} /></FormField>
    <FormField error={inventoryQuantityError(minimum)} id={`manage-minimum-${part.id}`} label="Минимум"><input inputMode="numeric" pattern="[0-9]*" value={minimum} onChange={event => setMinimum(event.target.value)} /></FormField>
    <label className="inventory-checkbox"><input checked={active} type="checkbox" onChange={event => setActive(event.target.checked)} /> Активна в парке</label>
    <Button busy={busy} disabled={!isInventoryQuantity(minimum)} type="submit">Сохранить</Button>
    {error ? <p className="form-error" role="alert">{error}</p> : null}
  </form>
}

export function InventoryManageView({ apiClient = api, parkId, refreshVersion = 0, role, selectedCatalogPartId, onSelectedCatalogPartIdChange }: InventoryManageViewProps) {
  const [items, setItems] = useState<InventoryCatalogSearchItem[]>([])
  const [components, setComponents] = useState<ManageComponent[]>([])
  const [catalogQuery, setCatalogQuery] = useState('')
  const [catalogMode, setCatalogMode] = useState<'active' | 'archived' | 'all'>('active')
  const [catalogOffset, setCatalogOffset] = useState(0)
  const [catalogTotal, setCatalogTotal] = useState(0)
  const [selectedPart, setSelectedPart] = useState<InventoryCatalogSearchItem | null>(null)
  const [archiveOpen, setArchiveOpen] = useState(false)
  const [internalSelectedId, setInternalSelectedId] = useState<number | null>(null)
  const [workflow, setWorkflow] = useState<ManageWorkflow>(null)
  const [draft, setDraft] = useState<Draft>(emptyDraft)
  const [componentName, setComponentName] = useState('')
  const [componentPhoto, setComponentPhoto] = useState<File | null>(null)
  const [componentPreview, setComponentPreview] = useState('')
  const [partPhoto, setPartPhoto] = useState<File | null>(null)
  const [partPreview, setPartPreview] = useState('')
  const [deletePart, setDeletePart] = useState<InventoryCatalogSearchItem | null>(null)
  const [deleteComponentId, setDeleteComponentId] = useState('')
  const [deleteComponentOpen, setDeleteComponentOpen] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [componentError, setComponentError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const generation = useRef(0)
  const accessGeneration = useRef(0)
  const operationGeneration = useRef(0)
  const activeParkId = useRef(parkId)
  const loadedCatalogCriteria = useRef<{ q?: string; mode: 'active' | 'archived' | 'all' }>({ mode: 'active' })
  const isSelectionControlled = selectedCatalogPartId !== undefined
  const selectedId = selectedCatalogPartId === undefined ? internalSelectedId : selectedCatalogPartId
  const canCreate = role === 'mechanic' || role === 'admin' || role === 'royal'
  const canManageGlobal = role === 'admin' || role === 'royal'
  const canDeleteGlobal = role === 'royal'
  const selected = selectedPart?.id === selectedId ? selectedPart : items.find(item => item.id === selectedId) ?? null
  const select = useCallback((id: number | null) => {
    if (!isSelectionControlled) setInternalSelectedId(id)
    onSelectedCatalogPartIdChange?.(id)
  }, [isSelectionControlled, onSelectedCatalogPartIdChange])
  const load = useCallback(async () => {
    const requestId = ++generation.current
    const query = catalogQuery.trim() || undefined
    setLoading(true)
    setError('')
    try {
      const value = await apiClient.searchInventory({ parkId, query, mode: catalogMode, limit: 25, offset: catalogOffset })
      if (requestId !== generation.current) return
      setItems(value.items)
      setCatalogTotal(value.total)
      loadedCatalogCriteria.current = { q: query, mode: catalogMode }
      setSelectedPart(current => value.items.find(item => item.id === current?.id) ?? current)
      setLoading(false)
    } catch (reason) {
      if (requestId !== generation.current) return
      const failure = classifyApiError(reason, 'Не удалось загрузить каталог.')
      setError(failure.description)
      if (failure.kind === 'unauthorized' || failure.kind === 'forbidden') {
        accessGeneration.current += 1
        operationGeneration.current += 1
        setItems([])
        setComponents([])
        setCatalogTotal(0)
        setSelectedPart(null)
        select(null)
        setWorkflow(null)
        setDraft(emptyDraft)
        setComponentName('')
        setComponentPhoto(null)
        setComponentPreview('')
        setPartPhoto(null)
        setPartPreview('')
        setArchiveOpen(false)
        setDeletePart(null)
        setDeleteComponentOpen(false)
        setNotice('')
        setBusy(false)
      }
      setLoading(false)
    }
  }, [apiClient, catalogMode, catalogOffset, catalogQuery, parkId, select])

  useEffect(() => {
    setItems([])
    activeParkId.current = parkId
    const requestGeneration = ++accessGeneration.current
    setWorkflow(null)
    setSelectedPart(null)
    setCatalogOffset(0)
    setCatalogMode('active')
    loadedCatalogCriteria.current = { mode: 'active' }
    select(null)
    operationGeneration.current += 1
    setBusy(false)
    setComponentError('')
    setComponentPhoto(null); setComponentPreview(''); setPartPhoto(null); setPartPreview(''); setDeletePart(null); setDeleteComponentId(''); setDeleteComponentOpen(false)
    loadInventoryComponents(apiClient, parkId).then(value => {
      if (activeParkId.current === parkId && accessGeneration.current === requestGeneration) setComponents(value.map(item => ({ id: item.id, name: item.name, has_photo: item.has_photo })))
    }).catch(() => { if (activeParkId.current === parkId && accessGeneration.current === requestGeneration) setComponentError(INVENTORY_COMPONENTS_INCOMPLETE) })
    return () => { generation.current += 1 }
  }, [apiClient, parkId, select])

  useEffect(() => () => { if (componentPreview) URL.revokeObjectURL(componentPreview) }, [componentPreview])
  useEffect(() => () => { if (partPreview) URL.revokeObjectURL(partPreview) }, [partPreview])

  useEffect(() => {
    const timer = globalThis.setTimeout(() => { void load() }, catalogQuery ? 200 : 0)
    return () => { globalThis.clearTimeout(timer); generation.current += 1 }
  }, [catalogQuery, load, refreshVersion])

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
  const createComponent = async (event: FormEvent) => {
    event.preventDefault()
    const requestedParkId = parkId
    const requestGeneration = operationGeneration.current
    setBusy(true)
    setError('')
    try {
      let created = await apiClient.createInventoryCatalogComponent({ park_id: requestedParkId, name: componentName })
      if (componentPhoto) created = await apiClient.replaceInventoryCatalogComponentPhoto(created.id, componentPhoto)
      if (activeParkId.current !== requestedParkId || operationGeneration.current !== requestGeneration) return
      setComponents(current => [...current.filter(component => component.id !== created.id), { id: created.id, name: created.name, has_photo: created.has_photo }]
        .sort((left, right) => left.name.localeCompare(right.name, 'ru') || left.id - right.id))
      setDraft(current => ({ ...current, componentId: String(created.id) }))
      setComponentName('')
      setComponentPhoto(null); setComponentPreview('')
      setWorkflow('create')
    } catch (reason) {
      if (activeParkId.current === requestedParkId && operationGeneration.current === requestGeneration) setError(classifyApiError(reason, 'Не удалось создать компоненту.').description)
    } finally {
      if (activeParkId.current === requestedParkId && operationGeneration.current === requestGeneration) setBusy(false)
    }
  }
  const createPart = async (event: FormEvent) => {
    event.preventDefault()
    const componentId = Number(draft.componentId)
    if (!componentId || !draft.name.trim() || !draft.article.trim() || !isInventoryQuantity(draft.minimum)) return
    setBusy(true)
    setError('')
    setNotice('')
    const requestedParkId = parkId
    const requestGeneration = operationGeneration.current
    try {
      let created = await apiClient.createInventoryCatalogPart({ park_id: requestedParkId, component_id: componentId, name: draft.name, article: draft.article })
      if (partPhoto) created = await apiClient.replaceInventoryCatalogPartPhoto(created.id, partPhoto)
      const stock = await apiClient.updateInventoryStock(requestedParkId, created.id, { minimum_quantity: draft.minimum, location: draft.location, is_active: true })
      if (activeParkId.current !== requestedParkId || operationGeneration.current !== requestGeneration || stock.park_id !== requestedParkId) return
      const component = components.find(item => item.id === componentId)
      setItems(current => [...current.filter(item => item.id !== created.id), { ...created, component_name: component?.name ?? '', quantity: stock.quantity, minimum_quantity: stock.minimum_quantity, location: stock.location, stock_is_active: stock.is_active }])
      select(created.id)
      setDraft(emptyDraft)
      setPartPhoto(null); setPartPreview('')
      setWorkflow('stock')
      setNotice('Позиция создана и добавлена в склад парка.')
    } catch (reason) {
      if (activeParkId.current !== requestedParkId || operationGeneration.current !== requestGeneration) return
      const detail = inventoryErrorDetail(reason)
      if (isInventoryDuplicateErrorDetail(detail) && detail.code === 'inventory_article_exists') {
        if (!items.some(item => item.id === detail.existing_part_id)) {
          try {
            const existing = await apiClient.getInventoryCatalogPart(requestedParkId, detail.existing_part_id)
            if (activeParkId.current !== requestedParkId || operationGeneration.current !== requestGeneration) return
            setItems(current => [...current.filter(item => item.id !== existing.id), existing])
            setSelectedPart(existing)
          } catch {
            if (activeParkId.current === requestedParkId && operationGeneration.current === requestGeneration) setError('Не удалось открыть существующую позицию.')
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
  const selectPhoto = (
    file: File | undefined,
    setFile: (value: File | null) => void,
    preview: string,
    setPreview: (value: string) => void,
  ) => {
    if (preview) URL.revokeObjectURL(preview)
    setFile(file ?? null)
    setPreview(file ? URL.createObjectURL(file) : '')
  }
  const applyCatalogDeletion = (summary: InventoryCatalogDeleteSummary) => {
    const deletedIds = new Set(summary.deleted_part_ids)
    setItems(current => current.filter(item => !deletedIds.has(item.id)))
    setCatalogTotal(current => Math.max(0, current - summary.matched_deleted_count))
  }
  const permanentlyDeletePart = async () => {
    if (!deletePart) return
    setBusy(true); setError('')
    try {
      const summary = await apiClient.permanentlyDeleteInventoryCatalogPart(deletePart.id, loadedCatalogCriteria.current)
      applyCatalogDeletion(summary)
      select(null); setSelectedPart(null); setWorkflow(null); setDeletePart(null)
      setNotice(`Позиция «${deletePart.name}» удалена навсегда.`)
    } catch (reason) { setError(classifyApiError(reason, 'Не удалось удалить позицию.').description) }
    finally { setBusy(false) }
  }
  const permanentlyDeleteComponent = async () => {
    const component = components.find(item => item.id === Number(deleteComponentId))
    if (!component) return
    setBusy(true); setError('')
    try {
      const summary = await apiClient.permanentlyDeleteInventoryCatalogComponent(component.id, loadedCatalogCriteria.current)
      setComponents(current => current.filter(item => item.id !== component.id))
      applyCatalogDeletion(summary)
      if (selected?.component_id === component.id) { select(null); setSelectedPart(null) }
      setDeleteComponentId(''); setDeleteComponentOpen(false); setWorkflow(null)
      setNotice(`Компонента «${component.name}» удалена навсегда.`)
    } catch (reason) { setError(classifyApiError(reason, 'Не удалось удалить компоненту.').description) }
    finally { setBusy(false) }
  }

  return <section className="inventory-manage-view rp-form-stack--mobile">
    <header className="inventory-manage-heading"><h2>{canManageGlobal ? 'Глобальный каталог' : 'Настройки склада парка'}</h2><p>{canManageGlobal ? 'Глобальные позиции и настройки склада выбранного парка.' : 'Параметры остатков выбранного парка.'}</p></header>
    <div className="inventory-manage-toolbar">
      <FormField id="inventory-manage-search" label="Найти позицию каталога"><input type="search" value={catalogQuery} onChange={event => { setCatalogQuery(event.target.value); setCatalogOffset(0) }} /></FormField>
      {canManageGlobal ? <FormField id="inventory-manage-mode" label="Состояние каталога"><select value={catalogMode} onChange={event => { setCatalogMode(event.target.value as 'active' | 'archived' | 'all'); setCatalogOffset(0); select(null); setSelectedPart(null); setWorkflow(null) }}><option value="active">Активные</option><option value="archived">Архивные</option><option value="all">Все</option></select></FormField> : null}
      <FormField id="inventory-manage-part" label="Позиция каталога"><select value={selectedId ?? ''} onChange={event => { const id = event.target.value ? Number(event.target.value) : null; select(id); setSelectedPart(items.find(item => item.id === id) ?? null); setWorkflow(null) }}><option value="">Выберите</option>{selected && !items.some(item => item.id === selected.id) ? <option value={selected.id}>{selected.name} · {selected.article}</option> : null}{items.map(item => <option key={item.id} value={item.id}>{item.name} · {item.article}</option>)}</select></FormField>
      <div className="inventory-card-actions rp-action-bar">
        {canCreate ? <><Button onClick={() => chooseWorkflow('create')} size="compact">Добавить позицию</Button><Button onClick={() => chooseWorkflow('component')} size="compact" variant="secondary">Добавить компоненту</Button>{canManageGlobal ? <Button onClick={() => chooseWorkflow('component-edit')} size="compact" variant="secondary">Редактировать компоненту</Button> : null}{canDeleteGlobal ? <Button onClick={() => chooseWorkflow('component-delete')} size="compact" variant="danger">Удалить компоненту</Button> : null}</> : null}
        {selected ? <Button onClick={() => chooseWorkflow('stock')} size="compact" variant="secondary">Настроить остаток</Button> : null}
        {canManageGlobal && selected ? <ResponsiveDisclosureGroup label="Глобальные действия"><ResponsiveDisclosure id="inventory-global-actions" title="Глобальные действия"><div className="inventory-global-actions"><Button onClick={() => chooseWorkflow('global-edit')} size="compact" variant="secondary">Редактировать глобально</Button>{selected.is_active ? <><Button onClick={() => setArchiveOpen(true)} size="compact" variant="danger">Архивировать глобально</Button><Button onClick={() => chooseWorkflow('merge')} size="compact" variant="danger">Объединить глобально</Button></> : <Button onClick={async () => { setBusy(true); setError(''); try { await apiClient.updateInventoryCatalogPart(selected.id, { is_active: true }); select(null); setSelectedPart(null); setNotice('Позиция восстановлена.'); await load() } catch (reason) { setError(classifyApiError(reason, 'Не удалось восстановить позицию.').description) } finally { setBusy(false) } }} size="compact" variant="secondary">Восстановить глобально</Button>}{canDeleteGlobal ? <Button onClick={() => setDeletePart(selected)} size="compact" variant="danger">Удалить навсегда</Button> : null}</div></ResponsiveDisclosure></ResponsiveDisclosureGroup> : null}
      </div>
    </div>
    {loading ? <LoadingState label="Загружаем каталог" /> : null}
    {catalogTotal > 25 || catalogOffset > 0 ? <nav aria-label="Страницы каталога" className="inventory-pagination"><Button disabled={catalogOffset === 0} onClick={() => setCatalogOffset(value => Math.max(0, value - 25))} size="compact" variant="secondary">Предыдущая страница каталога</Button><span>{items.length ? `${catalogOffset + 1}–${Math.min(catalogOffset + 25, catalogTotal)} из ${catalogTotal}` : `0 из ${catalogTotal}`}</span><Button disabled={catalogOffset + 25 >= catalogTotal} onClick={() => setCatalogOffset(value => value + 25)} size="compact" variant="secondary">Следующая страница каталога</Button></nav> : null}
    {notice ? <p className="inventory-notice" role="status">{notice}</p> : null}
    {componentError ? <p className="form-error" role="alert">{componentError}</p> : null}
    {error ? <p className="form-error" role="alert">{error}</p> : null}
    {workflow === 'component' ? <form aria-label="Новая компонента" className="inventory-manage-form" onSubmit={createComponent}><h3>Новая компонента</h3><FormField id="inventory-new-component" label="Название" required><input value={componentName} onChange={event => setComponentName(event.target.value)} /></FormField><PhotoFileAction hasPhoto={componentPhoto !== null} id="inventory-new-component-photo" onChange={file => selectPhoto(file, setComponentPhoto, componentPreview, setComponentPreview)} />{componentPreview ? <img alt="Предпросмотр фото компоненты" className="inventory-manage-photo" src={componentPreview} /> : null}<Button busy={busy} disabled={!componentName.trim()} type="submit">Создать</Button></form> : null}
    {workflow === 'component-edit' ? <GlobalComponentForm apiClient={apiClient} components={components} onChanged={updated => setComponents(current => current.map(component => component.id === updated.id ? updated : component))} /> : null}
    {workflow === 'component-delete' ? <div className="inventory-manage-form"><h3>Удалить компоненту</h3><FormField id="inventory-delete-component" label="Компонента для удаления"><select value={deleteComponentId} onChange={event => setDeleteComponentId(event.target.value)}><option value="">Выберите</option>{components.map(component => <option key={component.id} value={component.id}>{component.name}</option>)}</select></FormField><Button disabled={!deleteComponentId} onClick={() => setDeleteComponentOpen(true)} variant="danger">Удалить компоненту навсегда</Button></div> : null}
    {workflow === 'create' ? <form aria-label="Новая позиция" className="inventory-manage-form" onSubmit={createPart}><h3>Новая позиция</h3><FormField id="inventory-new-part-component" label="Компонента" required><select value={draft.componentId} onChange={event => setDraft(current => ({ ...current, componentId: event.target.value }))}><option value="">Выберите</option>{components.map(component => <option key={component.id} value={component.id}>{component.name}</option>)}</select></FormField><FormField id="inventory-new-part-name" label="Название" required><input value={draft.name} onChange={event => setDraft(current => ({ ...current, name: event.target.value }))} /></FormField><FormField id="inventory-new-part-article" label="Артикул" required><input value={draft.article} onChange={event => setDraft(current => ({ ...current, article: event.target.value }))} /></FormField><FormField id="inventory-new-part-location" label="Место"><input value={draft.location} onChange={event => setDraft(current => ({ ...current, location: event.target.value }))} /></FormField><FormField error={inventoryQuantityError(draft.minimum)} id="inventory-new-part-minimum" label="Минимум"><input inputMode="numeric" pattern="[0-9]*" value={draft.minimum} onChange={event => setDraft(current => ({ ...current, minimum: event.target.value }))} /></FormField><PhotoFileAction hasPhoto={partPhoto !== null} id="inventory-new-part-photo" onChange={file => selectPhoto(file, setPartPhoto, partPreview, setPartPreview)} />{partPreview ? <img alt="Предпросмотр фото позиции" className="inventory-manage-photo" src={partPreview} /> : null}<Button busy={busy} disabled={!draft.componentId || !draft.name.trim() || !draft.article.trim() || !isInventoryQuantity(draft.minimum)} type="submit">Создать</Button></form> : null}
    {workflow === 'stock' && selected ? <ParkStockForm apiClient={apiClient} onSaved={updateStockItem} parkId={parkId} part={selected} /> : null}
    {workflow === 'global-edit' && selected && canManageGlobal ? <GlobalPartForm apiClient={apiClient} onSaved={updated => { setItems(current => current.map(item => item.id === updated.id ? { ...item, ...updated } : item)); setSelectedPart(current => current?.id === updated.id ? { ...current, ...updated } : current) }} part={selected} /> : null}
    {workflow === 'merge' && selected && canManageGlobal ? <MergePartForm apiClient={apiClient} onSaved={async targetId => { select(targetId); await load(); setWorkflow(null) }} parkId={parkId} source={selected} /> : null}
    <ConfirmDialog confirmLabel="Подтвердить архивирование" description={selected ? `Позиция «${selected.name}» исчезнет из активного каталога.` : ''} onConfirm={async () => { if (!selected) return; setBusy(true); setError(''); try { await apiClient.updateInventoryCatalogPart(selected.id, { is_active: false }); setArchiveOpen(false); select(null); setSelectedPart(null); await load() } catch (reason) { setError(classifyApiError(reason, 'Не удалось архивировать позицию.').description) } finally { setBusy(false) } }} onOpenChange={setArchiveOpen} open={archiveOpen} pending={busy} title="Архивировать глобальную позицию?" tone="danger" />
    <ConfirmDialog confirmationPhrase={deletePart?.name} confirmLabel="Удалить навсегда" description="Будут удалены позиция, остатки, локальные акты и фото. История во внешнем Tracker не удаляется." onConfirm={permanentlyDeletePart} onOpenChange={open => { if (!open && !busy) setDeletePart(null) }} open={deletePart !== null} pending={busy} title={deletePart ? `Удалить позицию «${deletePart.name}» навсегда?` : ''} tone="danger" />
    <ConfirmDialog confirmationPhrase={components.find(item => item.id === Number(deleteComponentId))?.name} confirmLabel="Удалить навсегда" description="Будут удалены компонента, все её позиции, остатки, локальные акты и фото." onConfirm={permanentlyDeleteComponent} onOpenChange={setDeleteComponentOpen} open={deleteComponentOpen} pending={busy} title={deleteComponentId ? `Удалить компоненту «${components.find(item => item.id === Number(deleteComponentId))?.name}» навсегда?` : ''} tone="danger" />
  </section>
}

function GlobalPartForm({ apiClient, onSaved, part }: { apiClient: InventoryManageApi; onSaved: (part: InventoryCatalogSearchItem) => void | Promise<void>; part: InventoryCatalogSearchItem }) {
  const [name, setName] = useState(part.name)
  const [article, setArticle] = useState(part.article)
  const [hasPhoto, setHasPhoto] = useState(part.has_photo)
  const [photo, setPhoto] = useState<File | null>(null)
  const [preview, setPreview] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview) }, [preview])
  return <form aria-label="Глобальная позиция" className="inventory-manage-form" onSubmit={async event => { event.preventDefault(); setBusy(true); setError(''); try { let updated = await apiClient.updateInventoryCatalogPart(part.id, { name, article }); if (photo) { updated = await apiClient.replaceInventoryCatalogPartPhoto(part.id, photo); setPhoto(null); setPreview(''); setHasPhoto(true) } await onSaved({ ...part, ...updated }) } catch (reason) { setError(classifyApiError(reason, 'Не удалось изменить глобальную позицию.').description) } finally { setBusy(false) } }}><h3>Глобальная позиция</h3><FormField id={`global-name-${part.id}`} label="Название" required><input value={name} onChange={event => setName(event.target.value)} /></FormField><FormField id={`global-article-${part.id}`} label="Артикул" required><input value={article} onChange={event => setArticle(event.target.value)} /></FormField>{preview ? <img alt="Предпросмотр нового фото" className="inventory-manage-photo" src={preview} /> : hasPhoto ? <img alt={`Фото позиции «${part.name}»`} className="inventory-manage-photo" src={`/api/inventory/parts/${-part.id}/photo`} /> : null}<PhotoFileAction hasPhoto={hasPhoto || photo !== null} id={`global-photo-${part.id}`} onChange={next => { if (preview) URL.revokeObjectURL(preview); setPhoto(next ?? null); setPreview(next ? URL.createObjectURL(next) : '') }} /><div className="inventory-card-actions rp-action-bar"><Button busy={busy} type="submit">Сохранить</Button>{hasPhoto ? <Button disabled={busy} onClick={async () => { setBusy(true); setError(''); try { await apiClient.removeInventoryCatalogPartPhoto(part.id); setHasPhoto(false); setPhoto(null); setPreview(''); await onSaved({ ...part, has_photo: false }) } catch (reason) { setError(classifyApiError(reason, 'Не удалось удалить фото.').description) } finally { setBusy(false) } }} type="button" variant="ghost">Удалить</Button> : null}</div>{error ? <p className="form-error" role="alert">{error}</p> : null}</form>
}

function GlobalComponentForm({ apiClient, components, onChanged }: { apiClient: InventoryManageApi; components: ManageComponent[]; onChanged: (component: ManageComponent) => void }) {
  const [componentId, setComponentId] = useState('')
  const [photo, setPhoto] = useState<File | null>(null)
  const [preview, setPreview] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const component = components.find(item => item.id === Number(componentId)) ?? null
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview) }, [preview])
  const clearPreview = () => { if (preview) URL.revokeObjectURL(preview); setPreview(''); setPhoto(null) }
  return <form aria-label="Глобальная компонента" className="inventory-manage-form" onSubmit={async event => { event.preventDefault(); if (!component || !photo) return; setBusy(true); setError(''); try { const updated = await apiClient.replaceInventoryCatalogComponentPhoto(component.id, photo); clearPreview(); onChanged({ id: updated.id, name: updated.name, has_photo: updated.has_photo }) } catch (reason) { setError(classifyApiError(reason, 'Не удалось заменить фото компоненты.').description) } finally { setBusy(false) } }}><h3>Глобальная компонента</h3><FormField id="global-component" label="Компонента"><select value={componentId} onChange={event => { clearPreview(); setComponentId(event.target.value) }}><option value="">Выберите</option>{components.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></FormField>{preview ? <img alt="Предпросмотр нового фото компоненты" className="inventory-manage-photo" src={preview} /> : component?.has_photo ? <img alt={`Фото компоненты «${component.name}»`} className="inventory-manage-photo" src={`/api/inventory/components/${component.id}/photo`} /> : null}{component ? <PhotoFileAction hasPhoto={component.has_photo || photo !== null} id={`global-component-photo-${component.id}`} onChange={next => { clearPreview(); setPhoto(next ?? null); setPreview(next ? URL.createObjectURL(next) : '') }} /> : null}<div className="inventory-card-actions rp-action-bar"><Button busy={busy} disabled={!component || !photo} type="submit">Сохранить фото</Button>{component?.has_photo ? <Button disabled={busy} onClick={async () => { setBusy(true); setError(''); try { await apiClient.removeInventoryCatalogComponentPhoto(component.id); clearPreview(); onChanged({ ...component, has_photo: false }) } catch (reason) { setError(classifyApiError(reason, 'Не удалось удалить фото компоненты.').description) } finally { setBusy(false) } }} type="button" variant="ghost">Удалить</Button> : null}</div>{error ? <p className="form-error" role="alert">{error}</p> : null}</form>
}

function MergePartForm({ apiClient, onSaved, parkId, source }: { apiClient: InventoryManageApi; onSaved: (targetId: number) => void | Promise<void>; parkId: number; source: InventoryCatalogSearchItem }) {
  const [targetId, setTargetId] = useState('')
  const [query, setQuery] = useState('')
  const [offset, setOffset] = useState(0)
  const [targets, setTargets] = useState<InventoryCatalogSearchItem[]>([])
  const [total, setTotal] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const generation = useRef(0)
  useEffect(() => {
    const requestId = ++generation.current
    const timer = globalThis.setTimeout(() => {
      apiClient.searchInventory({ parkId, query: query.trim() || undefined, limit: 25, offset }).then(value => { if (requestId !== generation.current) return; setTargets(value.items.filter(item => item.id !== source.id)); setTotal(value.total) }).catch(reason => { if (requestId === generation.current) setError(classifyApiError(reason, 'Не удалось загрузить целевые позиции.').description) })
    }, query ? 200 : 0)
    return () => { globalThis.clearTimeout(timer); generation.current += 1 }
  }, [apiClient, offset, parkId, query, source.id])
  return <form aria-label="Объединение позиций" className="inventory-manage-form" onSubmit={async event => { event.preventDefault(); if (!targetId) return; setBusy(true); setError(''); try { await apiClient.mergeInventoryCatalogPart(source.id, Number(targetId)); await onSaved(Number(targetId)) } catch (reason) { setError(classifyApiError(reason, 'Не удалось объединить позиции.').description) } finally { setBusy(false) } }}><h3>Объединить «{source.name}»</h3><FormField id={`merge-search-${source.id}`} label="Найти целевую позицию"><input type="search" value={query} onChange={event => { setQuery(event.target.value); setOffset(0) }} /></FormField><FormField hint="Остатки и история перейдут в целевую позицию." id={`merge-target-${source.id}`} label="Целевая позиция" required><select value={targetId} onChange={event => setTargetId(event.target.value)}><option value="">Выберите</option>{targets.map(item => <option key={item.id} value={item.id}>{item.name} · {item.article}</option>)}</select></FormField>{total > 25 ? <div className="inventory-pagination"><Button disabled={offset === 0} onClick={() => setOffset(value => Math.max(0, value - 25))} size="compact" type="button" variant="secondary">Назад</Button><Button disabled={offset + 25 >= total} onClick={() => setOffset(value => value + 25)} size="compact" type="button" variant="secondary">Дальше</Button></div> : null}<Button busy={busy} disabled={!targetId} type="submit" variant="danger">Объединить</Button>{error ? <p className="form-error" role="alert">{error}</p> : null}</form>
}
