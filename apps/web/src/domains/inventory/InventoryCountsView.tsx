import { useCallback, useEffect, useRef, useState } from 'react'
import { api, inventoryErrorDetail, isInventoryCountStaleErrorDetail, type InventoryCatalogSearchItem, type InventoryCount, type InventoryCountScope, type InventoryPageEnvelope } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, LoadingState } from '../../design-system/feedback/AsyncState'
import { FormField } from '../../design-system/forms/FormField'
import { ConfirmDialog } from '../../design-system/overlays/ConfirmDialog'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { classifyApiError } from '../../shared/api/classifyApiError'
import { inventoryQuantityError, isInventoryQuantity } from './inventoryTypes'
import './inventory.css'

type CountsApi = Pick<typeof api, 'inventoryCounts' | 'searchInventory' | 'createInventoryCount' | 'updateInventoryCount' | 'postInventoryCount' | 'cancelInventoryCount'>
type Action = 'post' | 'cancel' | null
type Conflict = { catalog_part_id: number; expected_quantity: `${bigint}`; current_quantity: `${bigint}` }
const pageSize = 25
const labels = { draft: 'Черновик', posted: 'Проведён', cancelled: 'Отменён' } as const

export function InventoryCountsView({ apiClient = api, parkId, onInventoryChanged }: { apiClient?: CountsApi; parkId: number; onInventoryChanged?: () => void }) {
  const [page, setPage] = useState<InventoryPageEnvelope<InventoryCount> | null>(null)
  const [query, setQuery] = useState('')
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<InventoryCount | null>(null)
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [scope, setScope] = useState<InventoryCountScope>({ kind: 'all' })
  const [actual, setActual] = useState<Record<number, string>>({})
  const [parts, setParts] = useState<Record<number, InventoryCatalogSearchItem>>({})
  const [conflicts, setConflicts] = useState<Conflict[]>([])
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [action, setAction] = useState<Action>(null)
  const listGeneration = useRef(0)
  const catalogGeneration = useRef(0)
  const documentGeneration = useRef(0)
  const pending = useRef(false)
  const mobile = globalThis.matchMedia?.('(max-width: 599px)').matches ?? false

  const load = useCallback(() => {
    const generation = ++listGeneration.current
    apiClient.inventoryCounts(parkId, { query: query.trim() || undefined, limit: pageSize, offset }).then(value => {
      if (generation === listGeneration.current) setPage(value)
    }).catch(reason => { if (generation === listGeneration.current) setError(classifyApiError(reason, 'Не удалось загрузить акты.').description) })
  }, [apiClient, offset, parkId, query])
  useEffect(() => { load(); return () => { listGeneration.current += 1 } }, [load])
  useEffect(() => {
    const generation = ++catalogGeneration.current
    apiClient.searchInventory({ parkId, limit: 25, offset: 0 }).then(value => {
      if (generation === catalogGeneration.current) setParts(Object.fromEntries(value.items.map(part => [part.id, part])))
    }).catch(() => { /* Article fallback remains usable. */ })
    return () => { catalogGeneration.current += 1 }
  }, [apiClient, parkId])
  useEffect(() => {
    setPage(null); setSelected(null); setCreating(false); setName(''); setActual({}); setConflicts([]); setOffset(0); setNotice(''); setError('')
    documentGeneration.current += 1; pending.current = false
  }, [parkId])

  const open = (value: InventoryCount) => {
    setSelected(value); setCreating(false); setName(value.name); setActual(Object.fromEntries(value.lines.map(line => [line.catalog_part_id, line.actual_quantity ?? '']))); setConflicts([]); setError(''); setNotice('')
  }
  const create = async () => {
    if (!name.trim() || pending.current) return
    pending.current = true; setBusy(true); setError('')
    const generation = ++documentGeneration.current
    const requestedPark = parkId
    try {
      const value = await apiClient.createInventoryCount(requestedPark, { name: name.trim(), scope })
      if (generation !== documentGeneration.current || value.park_id !== requestedPark) return
      open(value); load()
    } catch (reason) { if (generation === documentGeneration.current) setError(classifyApiError(reason, 'Не удалось создать акт.').description) }
    finally { if (generation === documentGeneration.current) { setBusy(false); pending.current = false } }
  }
  const lines = selected?.lines ?? []
  const invalid = lines.length === 0 || lines.some(line => !isInventoryQuantity(actual[line.catalog_part_id] ?? ''))
  const commitAction = async () => {
    if (!selected || !action || pending.current) return
    pending.current = true; setBusy(true); setError(''); setConflicts([])
    const generation = ++documentGeneration.current
    const requestedPark = parkId
    const requestedId = selected.id
    try {
      let value: InventoryCount
      if (action === 'post') {
        if (invalid) return
        const saved = await apiClient.updateInventoryCount(requestedPark, requestedId, lines.map(line => ({ catalog_part_id: line.catalog_part_id, actual_quantity: actual[line.catalog_part_id] as `${bigint}`, comment: line.comment })))
        value = await apiClient.postInventoryCount(requestedPark, saved.id)
      } else value = await apiClient.cancelInventoryCount(requestedPark, requestedId)
      if (generation !== documentGeneration.current || value.park_id !== requestedPark || value.id !== requestedId) return
      open(value); setAction(null); setNotice(action === 'post' ? 'Акт проведён' : 'Акт отменён'); onInventoryChanged?.(); setOffset(0); load()
    } catch (reason) {
      if (generation !== documentGeneration.current) return
      const detail = inventoryErrorDetail(reason)
      if (isInventoryCountStaleErrorDetail(detail)) { setConflicts(detail.conflicts); setAction(null) }
      else setError(classifyApiError(reason, 'Не удалось изменить акт.').description)
    } finally { if (generation === documentGeneration.current) { setBusy(false); pending.current = false } }
  }
  const editorOpen = creating || selected !== null
  const listHidden = mobile && editorOpen

  return <section className="inventory-document-view"><h2 className="inventory-document-title">Инвентаризация</h2>
    <div className={`inventory-document-list${listHidden ? ' inventory-document-pane--hidden' : ''}`} hidden={listHidden}><div className="inventory-document-toolbar"><FormField id="count-search" label="Найти инвентаризацию"><input type="search" value={query} onChange={event => { setQuery(event.target.value); setOffset(0) }} /></FormField><Button onClick={() => { setCreating(true); setSelected(null); setName(''); setScope({ kind: 'all' }); setError(''); setNotice('') }}>Новая инвентаризация</Button></div>
      {!page ? <LoadingState label="Загружаем акты" /> : !page.items.length ? <EmptyState description="Создайте первый акт." icon="work" title="Актов нет" /> : <div className="inventory-document-cards">{page.items.map(item => <article aria-label={`Инвентаризация №${item.id}`} className="inventory-document-card" key={item.id}><div><strong>{item.name}</strong><p>Акт №{item.id} · {item.lines.length} позиций</p></div><StatusBadge tone={item.status === 'posted' ? 'success' : item.status === 'cancelled' ? 'neutral' : 'warning'}>{labels[item.status]}</StatusBadge><Button onClick={() => open(item)} size="compact" variant="secondary">Открыть</Button></article>)}</div>}
      {page && page.total > page.limit ? <nav aria-label="Страницы актов" className="inventory-pagination"><Button disabled={!offset} onClick={() => setOffset(value => Math.max(0, value - pageSize))} size="compact" variant="secondary">Предыдущая</Button><span>{offset + 1}–{Math.min(offset + page.limit, page.total)} из {page.total}</span><Button aria-label="Следующая страница актов" disabled={offset + page.limit >= page.total} onClick={() => setOffset(value => value + pageSize)} size="compact" variant="secondary">Следующая</Button></nav> : null}</div>
    {editorOpen ? <div className="inventory-document-editor"><Button className="inventory-document-back" onClick={() => { setCreating(false); setSelected(null) }} variant="ghost">Назад к актам</Button><h2>{creating ? 'Новая инвентаризация' : selected?.name}</h2>{notice ? <p className="inventory-notice" role="status">{notice}</p> : null}{error ? <p className="form-error" role="alert">{error}</p> : null}
      {creating ? <><FormField id="count-name" label="Название акта" required><input value={name} onChange={event => setName(event.target.value)} /></FormField><FormField id="count-scope" label="Охват"><select value={scope.kind} onChange={() => setScope({ kind: 'all' })}><option value="all">Весь склад</option></select></FormField><Button busy={busy} disabled={!name.trim()} onClick={create}>Создать акт</Button></> : selected ? <>{selected.status === 'draft' ? <><div className="inventory-document-lines">{lines.map(line => { const part = parts[line.catalog_part_id]; const article = part?.article ?? `#${line.catalog_part_id}`; const value = actual[line.catalog_part_id] ?? ''; const conflict = conflicts.find(item => item.catalog_part_id === line.catalog_part_id); const difference = isInventoryQuantity(value) ? BigInt(value) - BigInt(line.expected_quantity) : null; return <div className={`inventory-document-line${conflict ? ' inventory-document-line--conflict' : ''}`} key={line.id}><strong>{part?.name ?? 'Запчасть'} · {article}</strong><span>Ожидается: {line.expected_quantity}</span><FormField error={value ? inventoryQuantityError(value) : 'Укажите количество'} id={`count-actual-${line.catalog_part_id}`} label={`Фактически ${article}`}><input inputMode="numeric" value={value} onChange={event => setActual(current => ({ ...current, [line.catalog_part_id]: event.target.value }))} /></FormField>{difference !== null ? <strong>Разница: {difference > 0n ? '+' : ''}{difference.toString()}</strong> : null}{conflict ? <p className="form-error" role="alert">Ожидалось {conflict.expected_quantity}, сейчас {conflict.current_quantity}</p> : null}</div> })}</div><div className="inventory-document-actions"><Button disabled={invalid || busy} onClick={() => setAction('post')}>{conflicts.length ? 'Повторить проведение' : 'Провести акт'}</Button><Button disabled={busy} onClick={() => setAction('cancel')} variant="danger">Отменить акт</Button></div></> : <dl className="inventory-document-summary"><dt>Статус</dt><dd>{labels[selected.status]}</dd><dt>Позиций</dt><dd>{selected.lines.length}</dd></dl>}</> : null}
    </div> : !mobile ? <div className="inventory-document-empty"><p>Выберите акт или создайте новый.</p></div> : null}
    <ConfirmDialog confirmLabel={action === 'cancel' ? 'Подтвердить отмену' : 'Подтвердить проведение'} description="После проведения остатки будут изменены." onConfirm={commitAction} onOpenChange={open => { if (!open && !busy) setAction(null) }} open={action !== null} pending={busy} title="Подтвердите действие" tone={action === 'cancel' ? 'danger' : 'default'} />
  </section>
}
