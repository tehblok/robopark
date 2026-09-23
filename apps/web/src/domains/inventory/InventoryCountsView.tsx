import { useCallback, useEffect, useRef, useState } from 'react'
import { api, inventoryErrorDetail, isInventoryCountStaleErrorDetail, type InventoryCount, type InventoryCountScope, type InventoryPageEnvelope } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, LoadingState } from '../../design-system/feedback/AsyncState'
import { FormField } from '../../design-system/forms/FormField'
import { ConfirmDialog } from '../../design-system/overlays/ConfirmDialog'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { classifyApiError } from '../../shared/api/classifyApiError'
import { inventoryQuantityError, isInventoryQuantity } from './inventoryTypes'
import { INVENTORY_COMPONENTS_INCOMPLETE, loadInventoryComponents } from './loadInventoryComponents'
import './inventory.css'
import { InventorySecondaryActions } from './InventorySecondaryActions'

type CountsApi = Pick<typeof api, 'inventoryCounts' | 'inventoryCatalogComponents' | 'createInventoryCount' | 'updateInventoryCount' | 'refreshInventoryCount' | 'postInventoryCount' | 'cancelInventoryCount' | 'permanentlyDeleteInventoryCount'>
type Action = 'post' | 'cancel' | null
type Conflict = { catalog_part_id: number; expected_quantity: `${bigint}`; current_quantity: `${bigint}`; affected_lines?: Array<{ count_line_id: number; catalog_part_id: number }> }
const pageSize = 25
const labels = { draft: 'Черновик', posted: 'Проведён', cancelled: 'Отменён' } as const

export function InventoryCountsView({ apiClient = api, parkId, onInventoryChanged, permissions, refreshVersion = 0, role }: { apiClient?: CountsApi; parkId: number; onInventoryChanged?: () => void; permissions?: string[]; refreshVersion?: number; role?: string }) {
  const [page, setPage] = useState<InventoryPageEnvelope<InventoryCount> | null>(null)
  const [query, setQuery] = useState('')
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<InventoryCount | null>(null)
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [scope, setScope] = useState<InventoryCountScope>({ kind: 'all' })
  const [actual, setActual] = useState<Record<number, string>>({})
  const [dirtyLines, setDirtyLines] = useState<Set<number>>(() => new Set())
  const [components, setComponents] = useState<Array<{ id: number; name: string }>>([])
  const [componentError, setComponentError] = useState('')
  const [touched, setTouched] = useState<Set<number>>(() => new Set())
  const [conflicts, setConflicts] = useState<Conflict[]>([])
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [listError, setListError] = useState('')
  const [busy, setBusy] = useState(false)
  const [action, setAction] = useState<Action>(null)
  const [deleteTarget, setDeleteTarget] = useState<InventoryCount | null>(null)
  const listGeneration = useRef(0)
  const componentGeneration = useRef(0)
  const documentGeneration = useRef(0)
  const pending = useRef(false)
  const mobile = globalThis.matchMedia?.('(max-width: 599px)').matches ?? false
  const canManage = permissions === undefined || permissions.includes('inventory.stock.manage')
  const canPost = permissions === undefined || permissions.includes('inventory.documents.post')
  const canDelete = role === 'admin' || role === 'royal'

  const load = useCallback(() => {
    const generation = ++listGeneration.current
    setListError('')
    apiClient.inventoryCounts(parkId, { query: query.trim() || undefined, limit: pageSize, offset }).then(value => {
      if (generation === listGeneration.current) setPage(value)
    }).catch(reason => { if (generation === listGeneration.current) { setPage(null); setListError(classifyApiError(reason, 'Не удалось загрузить акты.').description) } })
  }, [apiClient, offset, parkId, query])
  useEffect(() => { load(); return () => { listGeneration.current += 1 } }, [load, refreshVersion])
  useEffect(() => {
    const generation = ++componentGeneration.current
    setComponentError('')
    loadInventoryComponents(apiClient, parkId).then(value => { if (generation === componentGeneration.current) setComponents(value) }).catch(() => { if (generation === componentGeneration.current) setComponentError(INVENTORY_COMPONENTS_INCOMPLETE) })
    return () => { componentGeneration.current += 1 }
  }, [apiClient, parkId])
  useEffect(() => {
    setPage(null); setSelected(null); setCreating(false); setName(''); setActual({}); setDirtyLines(new Set()); setConflicts([]); setTouched(new Set()); setOffset(0); setNotice(''); setError(''); setComponents([])
    documentGeneration.current += 1; pending.current = false
  }, [parkId])

  const open = (value: InventoryCount) => {
    setSelected(value); setCreating(false); setName(value.name); setActual(Object.fromEntries(value.lines.map(line => [line.catalog_part_id, line.actual_quantity ?? '']))); setDirtyLines(new Set()); setConflicts([]); setTouched(new Set()); setError(''); setNotice('')
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
  const changedLines = lines.filter(line => dirtyLines.has(line.id) && isInventoryQuantity(actual[line.catalog_part_id] ?? ''))
  const saveDraft = async () => {
    if (!selected || !changedLines.length || pending.current || !canManage) return
    pending.current = true; setBusy(true); setError('')
    const generation = ++documentGeneration.current
    const requestedPark = parkId
    const requestedId = selected.id
    try {
      const value = await apiClient.updateInventoryCount(requestedPark, requestedId, changedLines.map(line => ({ catalog_part_id: line.catalog_part_id, actual_quantity: actual[line.catalog_part_id] as `${bigint}`, comment: line.comment })))
      if (generation !== documentGeneration.current || value.park_id !== requestedPark || value.id !== requestedId) return
      open(value); setNotice('Черновик сохранён'); setOffset(0); load()
    } catch (reason) {
      if (generation === documentGeneration.current) setError(classifyApiError(reason, 'Не удалось сохранить черновик.').description)
    } finally {
      if (generation === documentGeneration.current) { setBusy(false); pending.current = false }
    }
  }
  const commitAction = async () => {
    if (!selected || !action || pending.current) return
    pending.current = true; setBusy(true); setError(''); setConflicts([])
    const generation = ++documentGeneration.current
    const requestedPark = parkId
    const requestedId = selected.id
    let latestDocument: InventoryCount | null = null
    try {
      let value: InventoryCount
      if (action === 'post') {
        if (invalid) return
        let saved: InventoryCount
        if (conflicts.length) {
          if (canManage) await apiClient.updateInventoryCount(requestedPark, requestedId, lines.map(line => ({ catalog_part_id: line.catalog_part_id, actual_quantity: actual[line.catalog_part_id] as `${bigint}`, comment: line.comment })))
          saved = await apiClient.refreshInventoryCount(requestedPark, requestedId)
          latestDocument = saved
        } else {
          saved = canManage
            ? await apiClient.updateInventoryCount(requestedPark, requestedId, lines.map(line => ({ catalog_part_id: line.catalog_part_id, actual_quantity: actual[line.catalog_part_id] as `${bigint}`, comment: line.comment })))
            : selected
        }
        value = await apiClient.postInventoryCount(requestedPark, saved.id)
      } else value = await apiClient.cancelInventoryCount(requestedPark, requestedId)
      if (generation !== documentGeneration.current || value.park_id !== requestedPark || value.id !== requestedId) return
      open(value); setAction(null); setNotice(action === 'post' ? 'Акт проведён' : 'Акт отменён'); onInventoryChanged?.(); setOffset(0); load()
    } catch (reason) {
      if (generation !== documentGeneration.current) return
      const detail = inventoryErrorDetail(reason)
      if (isInventoryCountStaleErrorDetail(detail)) { if (latestDocument) open(latestDocument); setConflicts(detail.conflicts); setAction(null) }
      else setError(classifyApiError(reason, 'Не удалось изменить акт.').description)
    } finally { if (generation === documentGeneration.current) { setBusy(false); pending.current = false } }
  }
  const editorOpen = creating || selected !== null
  const listHidden = mobile && editorOpen

  return <section className="inventory-document-view"><h2 className="inventory-document-title">Инвентаризация</h2>
    <div className={`inventory-document-list${listHidden ? ' inventory-document-pane--hidden' : ''}`} hidden={listHidden}><div className="inventory-document-toolbar"><FormField id="count-search" label="Найти инвентаризацию"><input type="search" value={query} onChange={event => { setQuery(event.target.value); setOffset(0) }} /></FormField>{canManage ? <Button onClick={() => { setCreating(true); setSelected(null); setName(''); setScope({ kind: 'all' }); setActual({}); setDirtyLines(new Set()); setError(''); setNotice(''); setTouched(new Set()) }}>Новая инвентаризация</Button> : null}</div>
      {listError ? <div><p className="form-error" role="alert">{listError}</p><Button onClick={load} size="compact" variant="secondary">Повторить загрузку актов</Button></div> : !page ? <LoadingState label="Загружаем акты" /> : !page.items.length ? <EmptyState description="Создайте первый акт." icon="work" title="Актов нет" /> : <div className="inventory-document-cards">{page.items.map(item => <article aria-label={`Инвентаризация №${item.id}`} className="inventory-document-card" key={item.id}><div><strong>{item.name}</strong><p>Акт №{item.id} · {item.lines.length} позиций</p></div><StatusBadge tone={item.status === 'posted' ? 'success' : item.status === 'cancelled' ? 'neutral' : 'warning'}>{labels[item.status]}</StatusBadge><Button onClick={() => open(item)} size="compact" variant="secondary">Открыть</Button>{canDelete ? <Button onClick={() => setDeleteTarget(item)} size="compact" variant="danger">Удалить навсегда</Button> : null}</article>)}</div>}
      {page && page.total > page.limit ? <nav aria-label="Страницы актов" className="inventory-pagination"><Button disabled={!offset} onClick={() => setOffset(value => Math.max(0, value - pageSize))} size="compact" variant="secondary">Предыдущая</Button><span>{offset + 1}–{Math.min(offset + page.limit, page.total)} из {page.total}</span><Button aria-label="Следующая страница актов" disabled={offset + page.limit >= page.total} onClick={() => setOffset(value => value + pageSize)} size="compact" variant="secondary">Следующая</Button></nav> : null}</div>
    {editorOpen ? <div className="inventory-document-editor"><Button className="inventory-document-back" disabled={busy} onClick={() => { setCreating(false); setSelected(null); setDirtyLines(new Set()) }} variant="ghost">Назад к актам</Button><h2>{creating ? 'Новая инвентаризация' : selected?.name}</h2>{notice ? <p className="inventory-notice" role="status">{notice}</p> : null}{error ? <p className="form-error" role="alert">{error}</p> : null}
      {creating ? <><FormField id="count-name" label="Название акта" required><input value={name} onChange={event => setName(event.target.value)} /></FormField><FormField id="count-scope" label="Охват"><select value={scope.kind === 'all' ? 'all' : String(scope.component_id)} onChange={event => setScope(event.target.value === 'all' ? { kind: 'all' } : { kind: 'component', component_id: Number(event.target.value) })}><option value="all">Весь склад</option>{components.map(component => <option key={component.id} value={component.id}>{component.name}</option>)}</select></FormField>{componentError ? <p className="form-error" role="alert">{componentError}</p> : null}<Button busy={busy} disabled={!name.trim()} onClick={create}>Создать акт</Button></> : selected ? <>{selected.status === 'draft' && canManage ? <><div className="inventory-document-lines">{lines.map(line => { const article = line.catalog_part_article; const value = actual[line.catalog_part_id] ?? ''; const conflict = conflicts.find(item => item.catalog_part_id === line.catalog_part_id || item.affected_lines?.some(affected => affected.count_line_id === line.id || affected.catalog_part_id === line.catalog_part_id)); const difference = isInventoryQuantity(value) ? BigInt(value) - BigInt(line.expected_quantity) : null; const validation = touched.has(line.id) ? (value ? inventoryQuantityError(value) : 'Укажите количество') : undefined; return <div className={`inventory-document-line${conflict ? ' inventory-document-line--conflict' : ''}`} key={line.id}><strong>{line.catalog_part_name} · {article}</strong><span>{line.catalog_component_name}</span><span>Ожидается: {line.expected_quantity}</span><FormField error={validation} id={`count-actual-${line.catalog_part_id}`} label={`Фактически ${article}`}><input inputMode="numeric" value={value} onBlur={() => setTouched(current => new Set(current).add(line.id))} onChange={event => { const next = event.target.value; setActual(current => ({ ...current, [line.catalog_part_id]: next })); setDirtyLines(current => { const updated = new Set(current); if (next === (line.actual_quantity ?? '')) updated.delete(line.id); else updated.add(line.id); return updated }) }} /></FormField>{difference !== null ? <strong>Разница: {difference > 0n ? '+' : ''}{difference.toString()}</strong> : null}{conflict ? <p className="form-error" role="alert">Ожидалось {conflict.expected_quantity}, сейчас {conflict.current_quantity}</p> : null}</div> })}</div><div className="inventory-document-actions"><Button busy={busy} disabled={!changedLines.length} onClick={saveDraft} variant="secondary">Сохранить черновик</Button>{canPost ? <Button disabled={invalid || busy} onClick={() => setAction('post')}>{conflicts.length ? 'Обновить остатки и провести' : 'Провести акт'}</Button> : null}<InventorySecondaryActions><Button disabled={busy} onClick={() => setAction('cancel')} variant="danger">Отменить акт</Button></InventorySecondaryActions></div></> : <><dl className="inventory-document-summary"><dt>Статус</dt><dd>{labels[selected.status]}</dd><dt>Позиций</dt><dd>{selected.lines.length}</dd></dl><div className="inventory-document-lines">{lines.map(line => { const conflict = conflicts.find(item => item.catalog_part_id === line.catalog_part_id || item.affected_lines?.some(affected => affected.count_line_id === line.id || affected.catalog_part_id === line.catalog_part_id)); return <div className={`inventory-document-line${conflict ? ' inventory-document-line--conflict' : ''}`} key={line.id}><strong>{line.catalog_part_name} · {line.catalog_part_article}</strong><span>{line.catalog_component_name}</span><span>Ожидается: {line.expected_quantity}</span><span>Фактически: {actual[line.catalog_part_id] || '—'}</span>{conflict ? <p className="form-error" role="alert">Ожидалось {conflict.expected_quantity}, сейчас {conflict.current_quantity}</p> : null}</div> })}</div>{selected.status === 'draft' && canPost ? <Button disabled={invalid || busy} onClick={() => setAction('post')}>{conflicts.length ? 'Обновить остатки и провести' : 'Провести акт'}</Button> : null}</>}</> : null}
    </div> : !mobile ? <div className="inventory-document-empty"><p>Выберите акт или создайте новый.</p></div> : null}
    <ConfirmDialog confirmLabel={action === 'cancel' ? 'Подтвердить отмену' : 'Подтвердить проведение'} description="После проведения остатки будут изменены." onConfirm={commitAction} onOpenChange={open => { if (!open && !busy) setAction(null) }} open={action !== null} pending={busy} title="Подтвердите действие" tone={action === 'cancel' ? 'danger' : 'default'} />
    <ConfirmDialog confirmationPhrase={deleteTarget?.name} confirmLabel="Удалить навсегда" description="Акт и его локальные строки будут удалены. История во внешнем Tracker не удаляется." onConfirm={async () => { if (!deleteTarget || pending.current) return; pending.current = true; setBusy(true); setError(''); try { await apiClient.permanentlyDeleteInventoryCount(deleteTarget.id); const deletingLastItem = page?.items.length === 1 && offset > 0; setPage(current => current ? { ...current, items: current.items.filter(item => item.id !== deleteTarget.id), total: Math.max(0, current.total - 1) } : current); if (deletingLastItem) setOffset(value => Math.max(0, value - pageSize)); if (selected?.id === deleteTarget.id) setSelected(null); setNotice(`Акт «${deleteTarget.name}» удалён навсегда.`); setDeleteTarget(null); onInventoryChanged?.() } catch (reason) { setError(classifyApiError(reason, 'Не удалось удалить акт.').description) } finally { setBusy(false); pending.current = false } }} onOpenChange={open => { if (!open && !busy) setDeleteTarget(null) }} open={deleteTarget !== null} pending={busy} title={deleteTarget ? `Удалить акт «${deleteTarget.name}» навсегда?` : ''} tone="danger" />
  </section>
}
