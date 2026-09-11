import { type FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { api, type InventoryOverview } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { classifyApiError } from '../../shared/api/classifyApiError'
import { inventoryInt64Compare, isPositiveInventoryQuantity } from './inventoryTypes'
import './inventory.css'

type TaskPartsApi = Pick<typeof api, 'inventory' | 'writeoffInventoryForTask' | 'inventoryComponentPhotoUrl' | 'inventoryPartPhotoUrl'>

export function TaskPartsPanel({ parkId, issueKey, apiClient = api, onWritten }: { parkId: number; issueKey: string; apiClient?: TaskPartsApi; onWritten?: () => void }) {
  const [data, setData] = useState<InventoryOverview | null>(null)
  const [componentId, setComponentId] = useState(0)
  const [partId, setPartId] = useState(0)
  const [quantity, setQuantity] = useState('1')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const load = useCallback(() => apiClient.inventory(parkId).then(value => { setData(value); setError(null) }).catch(setError), [apiClient, parkId])
  useEffect(() => { void load() }, [load])
  const component = data?.components.find(item => item.id === componentId)
  const part = useMemo(() => component?.parts.find(item => item.id === partId), [component, partId])
  useEffect(() => {
    if (!data) return
    const refreshedComponent = data.components.find(item => item.id === componentId)
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
  }, [componentId, data, partId, quantity])
  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (!part || !isPositiveInventoryQuantity(quantity)) return; setBusy(true); setError(null)
    try { await apiClient.writeoffInventoryForTask(issueKey, part.id, quantity); await load(); onWritten?.() }
    catch (reason) { setError(reason) }
    finally { setBusy(false) }
  }
  if (!data && !error) return <LoadingState label="Загружаем запчасти" />
  const failure = error ? classifyApiError(error, 'Не удалось списать запчасть.') : null
  return <div className="task-parts"><p>Выберите компоненту и запчасть. После списания в Tracker появится техническое сообщение.</p>
    {failure ? <ErrorState description={failure.description} title={failure.title} /> : null}
    {data ? <form className="form-grid" onSubmit={submit}><label className="field"><span>Компонента</span><select required value={componentId || ''} onChange={event => { setComponentId(Number(event.target.value)); setPartId(0) }}><option value="">Выберите</option>{data.components.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      {component?.has_photo ? <img alt={component.name} className="task-parts__component-photo" src={apiClient.inventoryComponentPhotoUrl(component.id)} /> : null}
      {component ? <label className="field"><span>Запчасть</span><select required value={partId || ''} onChange={event => setPartId(Number(event.target.value))}><option value="">Выберите</option>{component.parts.map(item => <option key={item.id} value={item.id}>{item.name} · {item.article} · {item.quantity} шт.</option>)}</select></label> : null}
      {part ? <article className="task-part-preview">{part.has_photo ? <img alt={part.name} src={apiClient.inventoryPartPhotoUrl(part.id)} /> : null}<div><h3>{part.name}</h3><p>Артикул: {part.article}</p><p>Место: <strong>{part.location}</strong></p><StatusBadge tone={part.quantity !== '0' ? 'success' : 'critical'}>{part.quantity !== '0' ? `На складе: ${part.quantity}` : 'Нет на складе'}</StatusBadge></div></article> : null}
      {part ? <label className="field"><span>Списать, шт.</span><input inputMode="numeric" onChange={event => setQuantity(event.target.value)} pattern="[0-9]*" value={quantity} /></label> : null}<Button busy={busy} disabled={!part || part.quantity === '0' || !isPositiveInventoryQuantity(quantity) || inventoryInt64Compare(quantity, part.quantity) > 0} type="submit">Списать в задачу</Button></form> : null}
  </div>
}
