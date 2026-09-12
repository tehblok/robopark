import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type InventoryOverview } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { classifyApiError } from '../../shared/api/classifyApiError'
import { inventoryInt64Compare, isPositiveInventoryQuantity } from './inventoryTypes'
import './inventory.css'

type TaskPartsApi = Pick<typeof api, 'inventory' | 'writeoffInventoryForTask' | 'inventoryComponentPhotoUrl' | 'inventoryPartPhotoUrl'> & {
  searchInventory?: typeof api.searchInventory
}

export function TaskPartsPanel({ parkId, issueKey, apiClient = api, onWritten }: { parkId: number | null; issueKey: string; apiClient?: TaskPartsApi; onWritten?: () => void }) {
  const [data, setData] = useState<InventoryOverview | null>(null)
  const [componentId, setComponentId] = useState(0)
  const [partId, setPartId] = useState(0)
  const [quantity, setQuantity] = useState('1')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [globalCatalog, setGlobalCatalog] = useState(false)
  const [total, setTotal] = useState(0)
  const [nextOffset, setNextOffset] = useState(0)
  const [loadingMore, setLoadingMore] = useState(false)
  const loadGeneration = useRef(0)
  const load = useCallback(async (offset = 0, append = false) => {
    if (parkId == null) return
    const generation = ++loadGeneration.current
    if (append) setLoadingMore(true)
    try {
      if (apiClient.searchInventory && (apiClient === api || apiClient.searchInventory !== api.searchInventory)) {
        const result = await apiClient.searchInventory({ parkId, stockFilter: 'in_stock', limit: 200, offset })
        if (generation !== loadGeneration.current) return
        setData(current => {
          const previous = append && current?.park_id === parkId ? current.components.flatMap(component => component.parts) : []
          const usefulItems = result.items.filter(item => item.is_active && item.stock_is_active && item.quantity !== '0')
          const page = usefulItems.map(item => ({
            id: item.id,
            park_id: parkId,
            component_id: item.component_id,
            name: item.name,
            article: item.article,
            quantity: item.quantity,
            minimum_quantity: item.minimum_quantity,
            location: item.location ?? 'Не указано',
            is_active: item.is_active && item.stock_is_active,
            has_photo: item.has_photo,
          }))
          const parts = Array.from(new Map([...previous, ...page].map(part => [part.id, part])).values())
          const componentNames = new Map<number, string>()
          if (append && current) current.components.forEach(component => componentNames.set(component.id, component.name))
          usefulItems.forEach(item => componentNames.set(item.component_id, item.component_name))
          const components = Array.from(componentNames.entries()).map(([id, name]) => ({ id, park_id: parkId, name, has_photo: false, parts: parts.filter(part => part.component_id === id) }))
          return { park_id: parkId, component_count: components.length, part_count: result.total, low_stock_count: 0, out_of_stock_count: 0, components }
        })
        setGlobalCatalog(true)
        setTotal(result.total)
        setNextOffset(result.offset + result.items.length)
        setError(null)
        return
      }
      const value = await apiClient.inventory(parkId)
      if (generation === loadGeneration.current) { setData(value); setGlobalCatalog(false); setTotal(value.part_count); setNextOffset(value.part_count); setError(null) }
    } catch (reason) {
      if (generation === loadGeneration.current) setError(reason)
    } finally {
      if (generation === loadGeneration.current) setLoadingMore(false)
    }
  }, [apiClient, parkId])
  useEffect(() => { setData(null); setTotal(0); setNextOffset(0); void load(); return () => { loadGeneration.current += 1 } }, [load])
  const currentData = data?.park_id === parkId ? data : null
  const component = currentData?.components.find(item => item.id === componentId)
  const part = useMemo(() => component?.parts.find(item => item.id === partId), [component, partId])
  useEffect(() => {
    if (!currentData) return
    const refreshedComponent = currentData.components.find(item => item.id === componentId)
    if (!refreshedComponent) {
      setComponentId(0); setPartId(0); setQuantity('1')
      return
    }
    const refreshedPart = refreshedComponent.parts.find(item => item.id === partId)
    if (!refreshedPart) {
      setPartId(0); setQuantity('1')
      return
    }
    if (isPositiveInventoryQuantity(quantity) && inventoryInt64Compare(quantity, refreshedPart.quantity) > 0) setQuantity('1')
  }, [componentId, currentData, partId, quantity])
  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (!part || !isPositiveInventoryQuantity(quantity)) return; setBusy(true); setError(null)
    try { await apiClient.writeoffInventoryForTask(issueKey, globalCatalog ? -part.id : part.id, quantity); await load(0, false); onWritten?.() }
    catch (reason) { setError(reason) }
    finally { setBusy(false) }
  }
  if (parkId == null) return <div className="task-parts"><ErrorState description="Откройте задачу из доступного вам парка." title="Парк задачи недоступен" /><Button disabled type="button">Списать в задачу</Button></div>
  if (!currentData && !error) return <LoadingState label="Загружаем запчасти" />
  const failure = error ? classifyApiError(error, 'Не удалось списать запчасть.') : null
  return <div className="task-parts"><p>Выберите компоненту и запчасть. После списания в Tracker появится техническое сообщение.</p>
    {failure ? <ErrorState description={failure.description} title={failure.title} /> : null}
    {currentData ? <form className="form-grid" onSubmit={submit}><label className="field"><span>Компонента</span><select required value={componentId || ''} onChange={event => { setComponentId(Number(event.target.value)); setPartId(0) }}><option value="">Выберите</option>{currentData.components.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      {component?.has_photo ? <img alt={component.name} className="task-parts__component-photo" src={apiClient.inventoryComponentPhotoUrl(component.id)} /> : null}
      {component ? <label className="field"><span>Запчасть</span><select required value={partId || ''} onChange={event => setPartId(Number(event.target.value))}><option value="">Выберите</option>{component.parts.map(item => <option key={item.id} value={item.id}>{item.name} · {item.article} · {item.quantity} шт.</option>)}</select></label> : null}
      {part ? <article className="task-part-preview">{part.has_photo ? <img alt={part.name} src={apiClient.inventoryPartPhotoUrl(globalCatalog ? -part.id : part.id)} /> : null}<div><h3>{part.name}</h3><p>Артикул: {part.article}</p><p>Место: <strong>{part.location}</strong></p><StatusBadge tone={part.quantity !== '0' ? 'success' : 'critical'}>{part.quantity !== '0' ? `На складе: ${part.quantity}` : 'Нет на складе'}</StatusBadge></div></article> : null}
      {part ? <label className="field"><span>Списать, шт.</span><input inputMode="numeric" onChange={event => setQuantity(event.target.value)} pattern="[0-9]*" value={quantity} /></label> : null}{globalCatalog && nextOffset < total ? <Button busy={loadingMore} onClick={() => void load(nextOffset, true)} type="button" variant="secondary">Загрузить ещё</Button> : null}<Button busy={busy} disabled={!part || part.quantity === '0' || !isPositiveInventoryQuantity(quantity) || inventoryInt64Compare(quantity, part.quantity) > 0} type="submit">Списать в задачу</Button></form> : null}
  </div>
}
