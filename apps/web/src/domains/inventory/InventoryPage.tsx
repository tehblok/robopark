import { type FormEvent, useCallback, useEffect, useState } from 'react'
import { api, type InventoryComponent, type InventoryOverview, type InventoryPart } from '../../api'
import { useParkScope } from '../../app/park/parkScope'
import { Button } from '../../design-system/actions/Button'
import { MetricCard } from '../../design-system/data/MetricCard'
import { EmptyState, ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { PageLayout, Panel } from '../../design-system/layout/PageLayout'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { classifyApiError } from '../../shared/api/classifyApiError'
import './inventory.css'

type InventoryApi = Pick<typeof api, 'inventory' | 'createInventoryComponent' | 'createInventoryPart' | 'updateInventoryPart' | 'moveInventoryStock' | 'inventoryComponentPhotoUrl' | 'inventoryPartPhotoUrl'>

export function visibleComponents(data: InventoryOverview, componentId: 'all' | number): InventoryComponent[] {
  return componentId === 'all'
    ? data.components
    : data.components.filter(component => component.id === componentId)
}

function StockEditor({ part, apiClient, reload }: { part: InventoryPart; apiClient: InventoryApi; reload: () => void }) {
  const [kind, setKind] = useState<'receipt' | 'writeoff'>('receipt')
  const [quantity, setQuantity] = useState(1)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError('')
    try { await apiClient.moveInventoryStock(part.id, kind, quantity); reload() }
    catch (reason) { setError(classifyApiError(reason, 'Не удалось изменить остаток.').description) }
    finally { setBusy(false) }
  }
  return <form className="inventory-stock-editor" onSubmit={submit}><select aria-label="Операция" value={kind} onChange={event => setKind(event.target.value as 'receipt' | 'writeoff')}><option value="receipt">Приход</option><option value="writeoff">Списание</option></select><input aria-label="Количество" min={1} onChange={event => setQuantity(Number(event.target.value))} type="number" value={quantity} /><Button busy={busy} size="compact" type="submit">Провести</Button>{error ? <span className="form-error" role="alert">{error}</span> : null}</form>
}

function PartCard({ part, componentName, apiClient, reload, onPrint }: { part: InventoryPart; componentName: string; apiClient: InventoryApi; reload: () => void; onPrint: (part: InventoryPart) => void }) {
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(part.name)
  const [article, setArticle] = useState(part.article)
  const [location, setLocation] = useState(part.location)
  const [minimum, setMinimum] = useState(part.minimum_quantity)
  const low = part.quantity <= part.minimum_quantity
  const save = async (event: FormEvent) => { event.preventDefault(); await apiClient.updateInventoryPart(part.id, { name, article, location, minimum_quantity: minimum }); setEditing(false); reload() }
  return <article className="inventory-part">
    {part.has_photo ? <img alt={part.name} className="inventory-part__photo" src={apiClient.inventoryPartPhotoUrl(part.id)} /> : <div className="inventory-photo-placeholder">Нет фото</div>}
    <div className="inventory-part__body"><div className="inventory-part__head"><div><h3>{part.name}</h3><p>Артикул: <strong>{part.article}</strong></p></div><StatusBadge tone={part.quantity === 0 ? 'critical' : low ? 'warning' : 'success'}>{part.quantity === 0 ? 'Нет на складе' : `${part.quantity} шт.`}</StatusBadge></div>
      <p>Место: <strong>{part.location}</strong> · минимум {part.minimum_quantity}</p>
      <div className="inventory-actions"><Button onClick={() => onPrint(part)} size="compact" variant="secondary">Распечатать этикетку</Button><Button onClick={() => setEditing(value => !value)} size="compact" variant="ghost">Редактировать</Button></div>
      {editing ? <form className="form-grid inventory-edit" onSubmit={save}><label className="field"><span>Название</span><input required value={name} onChange={event => setName(event.target.value)} /></label><label className="field"><span>Артикул</span><input required value={article} onChange={event => setArticle(event.target.value)} /></label><label className="field"><span>Место</span><input required value={location} onChange={event => setLocation(event.target.value)} /></label><label className="field"><span>Минимум</span><input min={0} type="number" value={minimum} onChange={event => setMinimum(Number(event.target.value))} /></label><Button type="submit">Сохранить</Button></form> : null}
      <StockEditor apiClient={apiClient} part={part} reload={reload} />
    </div>
    <span className="inventory-component-name">{componentName}</span>
  </article>
}

function CreateForms({ data, parkId, apiClient, reload }: { data: InventoryOverview; parkId: number; apiClient: InventoryApi; reload: () => void }) {
  const [componentName, setComponentName] = useState('')
  const [componentPhoto, setComponentPhoto] = useState<File | null>(null)
  const [componentId, setComponentId] = useState(data.components[0]?.id ?? 0)
  const [part, setPart] = useState({ name: '', article: '', quantity: 0, minimum: 0, location: '' })
  const [partPhoto, setPartPhoto] = useState<File | null>(null)
  useEffect(() => { if (!componentId && data.components[0]) setComponentId(data.components[0].id) }, [componentId, data.components])
  return <div className="inventory-create-grid"><Panel collapsible defaultCollapsed storageKey="inventory-component-create" title="Добавить компоненту"><form className="form-grid" onSubmit={async event => { event.preventDefault(); await apiClient.createInventoryComponent(parkId, componentName, componentPhoto); setComponentName(''); reload() }}><label className="field"><span>Название, например «Подвязка»</span><input required value={componentName} onChange={event => setComponentName(event.target.value)} /></label><label className="field"><span>Общее фото</span><input accept="image/*" type="file" onChange={event => setComponentPhoto(event.target.files?.[0] ?? null)} /></label><Button type="submit">Добавить</Button></form></Panel>
    <Panel collapsible defaultCollapsed storageKey="inventory-part-create" title="Завести запчасть"><form className="form-grid" onSubmit={async event => { event.preventDefault(); await apiClient.createInventoryPart({ park_id: parkId, component_id: componentId, name: part.name, article: part.article, quantity: part.quantity, minimum_quantity: part.minimum, location: part.location, photo: partPhoto }); setPart({ name: '', article: '', quantity: 0, minimum: 0, location: '' }); reload() }}><label className="field"><span>Компонента запчасти</span><select required value={componentId || ''} onChange={event => setComponentId(Number(event.target.value))}><option value="">Выберите</option>{data.components.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>{(['name', 'article', 'location'] as const).map(field => <label className="field" key={field}><span>{{ name: 'Название', article: 'Артикул', location: 'Место хранения' }[field]}</span><input required value={part[field]} onChange={event => setPart(current => ({ ...current, [field]: event.target.value }))} /></label>)}<label className="field"><span>Начальное количество</span><input min={0} type="number" value={part.quantity} onChange={event => setPart(current => ({ ...current, quantity: Number(event.target.value) }))} /></label><label className="field"><span>Минимальное количество</span><input min={0} type="number" value={part.minimum} onChange={event => setPart(current => ({ ...current, minimum: Number(event.target.value) }))} /></label><label className="field"><span>Фото запчасти</span><input accept="image/*" type="file" onChange={event => setPartPhoto(event.target.files?.[0] ?? null)} /></label><Button disabled={!componentId} type="submit">Завести</Button></form></Panel></div>
}

export function InventoryPage({ apiClient = api }: { apiClient?: InventoryApi }) {
  const { selectedPark, loading } = useParkScope()
  const [data, setData] = useState<InventoryOverview | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [printPart, setPrintPart] = useState<InventoryPart | null>(null)
  const [componentId, setComponentId] = useState<'all' | number>('all')
  const load = useCallback(() => { if (!selectedPark) return; setError(null); apiClient.inventory(selectedPark.id).then(setData).catch(setError) }, [apiClient, selectedPark])
  useEffect(load, [load])
  const print = (part: InventoryPart) => { setPrintPart(part); globalThis.setTimeout(() => window.print(), 0) }
  if (loading) return <LoadingState label="Загружаем парк" variant="page" />
  if (!selectedPark) return <EmptyState description="Выберите парк." icon="parks" title="Парк не выбран" />
  const failure = error ? classifyApiError(error, 'Не удалось загрузить склад.') : null
  return <PageLayout description={`Учёт запчастей парка «${selectedPark.name}»`} title="Склад">
    {failure ? <ErrorState description={failure.description} onRetry={load} title={failure.title} /> : !data ? <LoadingState label="Загружаем склад" variant="page" /> : <>
      <div className="stat-grid"><MetricCard label="Компоненты" value={data.component_count} /><MetricCard label="Запчасти" value={data.part_count} /><MetricCard label="Ниже минимума" tone={data.low_stock_count ? 'warning' : 'neutral'} value={data.low_stock_count} /><MetricCard label="Нет на складе" tone={data.out_of_stock_count ? 'critical' : 'neutral'} value={data.out_of_stock_count} /></div>
      <CreateForms apiClient={apiClient} data={data} parkId={selectedPark.id} reload={load} />
      <label className="field inventory-component-filter"><span>Компонента</span><select value={componentId} onChange={event => setComponentId(event.target.value === 'all' ? 'all' : Number(event.target.value))}><option value="all">Все компоненты</option>{data.components.map(component => <option key={component.id} value={component.id}>{component.name}</option>)}</select></label>
      <div className="inventory-components">{visibleComponents(data, componentId).map(component => <Panel key={component.id} collapsible storageKey={`inventory-component-${component.id}`} title={component.name}>{component.has_photo ? <img alt={`Компонента ${component.name}`} className="inventory-component-photo" src={apiClient.inventoryComponentPhotoUrl(component.id)} /> : null}<div className="inventory-parts">{component.parts.map(part => <PartCard apiClient={apiClient} componentName={component.name} key={part.id} onPrint={print} part={part} reload={load} />)}{!component.parts.length ? <p>Запчастей в этой компоненте пока нет.</p> : null}</div></Panel>)}{!data.components.length ? <EmptyState description="Добавьте первую компоненту и запчасть." icon="work" title="Склад пуст" /> : null}</div>
    </>}
    {printPart ? <div className="inventory-print-label"><strong>{printPart.name}</strong><span>Артикул: {printPart.article}</span><b>{printPart.location}</b></div> : null}
  </PageLayout>
}
