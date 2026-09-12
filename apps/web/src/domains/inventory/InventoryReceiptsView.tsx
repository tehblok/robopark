import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type InventoryCatalogSearchItem, type InventoryPageEnvelope, type InventoryReceipt, type InventoryReceiptInput } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, LoadingState } from '../../design-system/feedback/AsyncState'
import { FormField } from '../../design-system/forms/FormField'
import { ConfirmDialog } from '../../design-system/overlays/ConfirmDialog'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { classifyApiError } from '../../shared/api/classifyApiError'
import { inventoryQuantityError, isPositiveInventoryQuantity } from './inventoryTypes'
import './inventory.css'

type ReceiptsApi = Pick<typeof api, 'inventoryReceipts' | 'searchInventory' | 'createInventoryReceipt' | 'updateInventoryReceipt' | 'postInventoryReceipt' | 'cancelInventoryReceipt' | 'reverseInventoryReceipt'>
type DraftLine = { part: InventoryCatalogSearchItem; quantity: string; note: string }
type Action = 'post' | 'cancel' | 'reverse' | null
const pageSize = 25
const today = () => new Date().toISOString().slice(0, 10)
const statusLabel = { draft: 'Черновик', posted: 'Проведена', cancelled: 'Отменена' } as const

export function InventoryReceiptsView({ apiClient = api, parkId, onInventoryChanged, permissions }: { apiClient?: ReceiptsApi; parkId: number; onInventoryChanged?: () => void; permissions?: string[] }) {
  const [page, setPage] = useState<InventoryPageEnvelope<InventoryReceipt> | null>(null)
  const [query, setQuery] = useState('')
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<InventoryReceipt | null>(null)
  const [creating, setCreating] = useState(false)
  const [supplier, setSupplier] = useState('')
  const [documentNumber, setDocumentNumber] = useState('')
  const [receivedOn, setReceivedOn] = useState(today)
  const [comment, setComment] = useState('')
  const [lines, setLines] = useState<DraftLine[]>([])
  const [touchedLines, setTouchedLines] = useState<Set<number>>(() => new Set())
  const [partQuery, setPartQuery] = useState('')
  const [parts, setParts] = useState<InventoryCatalogSearchItem[]>([])
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [listError, setListError] = useState('')
  const [action, setAction] = useState<Action>(null)
  const [reverseReason, setReverseReason] = useState('')
  const [busy, setBusy] = useState(false)
  const listGeneration = useRef(0)
  const partGeneration = useRef(0)
  const operationGeneration = useRef(0)
  const pending = useRef(false)
  const postRetryId = useRef<number | null>(null)
  const mobile = globalThis.matchMedia?.('(max-width: 599px)').matches ?? false
  const canManage = permissions === undefined || permissions.includes('inventory.stock.manage')
  const canPost = permissions === undefined || permissions.includes('inventory.documents.post')

  const load = useCallback(() => {
    const generation = ++listGeneration.current
    setListError('')
    apiClient.inventoryReceipts(parkId, { query: query.trim() || undefined, limit: pageSize, offset }).then(value => {
      if (generation === listGeneration.current) setPage(value)
    }).catch(reason => { if (generation === listGeneration.current) { setPage(null); setListError(classifyApiError(reason, 'Не удалось загрузить поставки.').description) } })
  }, [apiClient, offset, parkId, query])

  useEffect(() => { load(); return () => { listGeneration.current += 1 } }, [load])
  useEffect(() => {
    setPage(null); setSelected(null); setCreating(false); setLines([]); setTouchedLines(new Set()); setOffset(0); setNotice(''); setError('')
    operationGeneration.current += 1; partGeneration.current += 1; pending.current = false; postRetryId.current = null; setReverseReason('')
  }, [parkId])
  useEffect(() => {
    if (!(creating || selected?.status === 'draft') || !partQuery.trim()) { setParts([]); return }
    const generation = ++partGeneration.current
    apiClient.searchInventory({ parkId, query: partQuery.trim(), limit: 25, offset: 0 }).then(value => {
      if (generation === partGeneration.current) setParts(value.items)
    }).catch(reason => { if (generation === partGeneration.current) setError(classifyApiError(reason, 'Не удалось найти запчасти.').description) })
    return () => { partGeneration.current += 1 }
  }, [apiClient, creating, parkId, partQuery, selected?.status])

  const receiptLines = (receipt: InventoryReceipt, known: DraftLine[] = []) => receipt.lines.map(line => ({
    part: known.find(item => item.part.id === line.catalog_part_id)?.part ?? { id: line.catalog_part_id, article: line.catalog_part_article, name: line.catalog_part_name, component_id: line.catalog_component_id, component_name: line.catalog_component_name, is_active: true, has_photo: false, quantity: '0' as const, minimum_quantity: '0' as const, location: null, stock_is_active: true },
    quantity: line.quantity,
    note: line.note ?? '',
  }))

  const begin = () => {
    setCreating(true); setSelected(null); setSupplier(''); setDocumentNumber(''); setReceivedOn(today()); setComment(''); setLines([]); setTouchedLines(new Set()); setPartQuery(''); setParts([]); setError(''); setNotice(''); setReverseReason(''); postRetryId.current = null
  }
  const open = (receipt: InventoryReceipt) => {
    setSelected(receipt); setCreating(false); setSupplier(receipt.supplier ?? ''); setDocumentNumber(receipt.document_number ?? ''); setReceivedOn(receipt.received_on); setComment(receipt.comment ?? ''); setLines(receiptLines(receipt)); setTouchedLines(new Set()); setError(''); setNotice(''); setReverseReason(''); postRetryId.current = null
  }
  const addLine = (part: InventoryCatalogSearchItem) => { postRetryId.current = null; setLines(current => {
    const existing = current.find(line => line.part.id === part.id)
    if (!existing) return [...current, { part, quantity: '1', note: '' }]
    return current.map(line => line.part.id === part.id ? { ...line, quantity: (BigInt(line.quantity || '0') + 1n).toString() } : line)
  }) }
  const draftLines = lines
  const invalid = !supplier.trim() || !receivedOn || !draftLines.length || draftLines.some(line => !isPositiveInventoryQuantity(line.quantity))
  const payload = (): InventoryReceiptInput => ({ supplier: supplier.trim() || null, document_number: documentNumber.trim() || null, received_on: receivedOn, comment: comment.trim() || null, lines: draftLines.map(line => ({ catalog_part_id: line.part.id, quantity: line.quantity as `${bigint}`, note: line.note.trim() || null })) })
  const saveDraft = async () => {
    if (invalid || pending.current || !canManage) return
    pending.current = true; setBusy(true); setError('')
    const generation = ++operationGeneration.current
    const requestedPark = parkId
    const requestedId = selected?.id ?? null
    try {
      const value = requestedId
        ? await apiClient.updateInventoryReceipt(requestedPark, requestedId, payload())
        : await apiClient.createInventoryReceipt(requestedPark, payload())
      if (generation !== operationGeneration.current || value.park_id !== requestedPark) return
      setSelected(value); setCreating(false); setLines(receiptLines(value, draftLines)); postRetryId.current = value.id; setNotice('Черновик сохранён'); setOffset(0); load()
    } catch (reason) {
      if (generation === operationGeneration.current) setError(classifyApiError(reason, 'Не удалось сохранить черновик.').description)
    } finally {
      if (generation === operationGeneration.current) { setBusy(false); pending.current = false }
    }
  }
  const commitAction = async () => {
    if (!action || pending.current) return
    pending.current = true; setBusy(true); setError('')
    const generation = ++operationGeneration.current
    const requestedPark = parkId
    const requestedId = selected?.id ?? null
    try {
      let value: InventoryReceipt
      if (action === 'post') {
        if (invalid) return
        const saved = canManage
          ? requestedId
            ? postRetryId.current === requestedId ? selected! : await apiClient.updateInventoryReceipt(requestedPark, requestedId, payload())
            : await apiClient.createInventoryReceipt(requestedPark, payload())
          : selected!
        if (generation !== operationGeneration.current || saved.park_id !== requestedPark) return
        setSelected(saved); setCreating(false); setLines(receiptLines(saved, draftLines)); postRetryId.current = saved.id
        value = await apiClient.postInventoryReceipt(requestedPark, saved.id)
      } else if (action === 'cancel' && requestedId) value = await apiClient.cancelInventoryReceipt(requestedPark, requestedId)
      else if (action === 'reverse' && requestedId) value = await apiClient.reverseInventoryReceipt(requestedPark, requestedId, reverseReason.trim())
      else return
      if (generation !== operationGeneration.current || value.park_id !== requestedPark) return
      setSelected(value); setCreating(false); setLines(receiptLines(value, draftLines)); setAction(null); setReverseReason(''); postRetryId.current = null; setNotice(action === 'post' ? 'Поставка проведена' : action === 'cancel' ? 'Поставка отменена' : 'Поставка сторнирована')
      onInventoryChanged?.(); setOffset(0); load()
    } catch (reason) {
      if (generation === operationGeneration.current) { setAction(null); setError(classifyApiError(reason, 'Не удалось изменить поставку.').description) }
    } finally {
      if (generation === operationGeneration.current) { setBusy(false); pending.current = false }
    }
  }
  const editorOpen = creating || selected !== null
  const listHidden = mobile && editorOpen
  const editorLines = creating || selected?.status === 'draft' ? draftLines : []

  return <section className="inventory-document-view"><h2 className="inventory-document-title">Поставки</h2>
    <div className={`inventory-document-list${listHidden ? ' inventory-document-pane--hidden' : ''}`} hidden={listHidden}>
      <div className="inventory-document-toolbar"><FormField id="receipt-search" label="Найти поставку"><input type="search" value={query} onChange={event => { setQuery(event.target.value); setOffset(0) }} /></FormField>{canManage ? <Button onClick={begin}>Новая поставка</Button> : null}</div>
      {listError ? <div><p className="form-error" role="alert">{listError}</p><Button onClick={load} size="compact" variant="secondary">Повторить загрузку поставок</Button></div> : !page ? <LoadingState label="Загружаем поставки" /> : !page.items.length ? <EmptyState description="Создайте первую поставку." icon="work" title="Поставок нет" /> : <div className="inventory-document-cards">{page.items.map(item => <article aria-label={`Поставка №${item.id}`} className="inventory-document-card" key={item.id}><div><strong>№{item.id}</strong><p>{item.supplier || 'Без поставщика'} · {item.received_on}</p></div><StatusBadge tone={item.status === 'posted' ? 'success' : item.status === 'cancelled' ? 'neutral' : 'warning'}>{statusLabel[item.status]}</StatusBadge><Button onClick={() => open(item)} size="compact" variant="secondary">Открыть</Button></article>)}</div>}
      {page && page.total > page.limit ? <nav aria-label="Страницы поставок" className="inventory-pagination"><Button disabled={!offset} onClick={() => setOffset(value => Math.max(0, value - pageSize))} size="compact" variant="secondary">Предыдущая</Button><span>{offset + 1}–{Math.min(offset + page.limit, page.total)} из {page.total}</span><Button aria-label="Следующая страница поставок" disabled={offset + page.limit >= page.total} onClick={() => setOffset(value => value + pageSize)} size="compact" variant="secondary">Следующая</Button></nav> : null}
    </div>
    {editorOpen ? <div className="inventory-document-editor"><Button className="inventory-document-back" disabled={busy} onClick={() => { setCreating(false); setSelected(null); setReverseReason(''); postRetryId.current = null }} variant="ghost">Назад к поставкам</Button><h2>{creating ? 'Новая поставка' : `Поставка №${selected?.id}`}</h2>
      {notice ? <p className="inventory-notice" role="status">{notice}</p> : null}{error ? <p className="form-error" role="alert">{error}</p> : null}
      {(creating || selected?.status === 'draft') && canManage ? <><div className="inventory-document-fields"><FormField id="receipt-supplier" label="Поставщик или завод" required><input value={supplier} onChange={event => { postRetryId.current = null; setSupplier(event.target.value) }} /></FormField><FormField id="receipt-number" label="Номер документа"><input value={documentNumber} onChange={event => { postRetryId.current = null; setDocumentNumber(event.target.value) }} /></FormField><FormField id="receipt-date" label="Дата поставки"><input type="date" value={receivedOn} onChange={event => { postRetryId.current = null; setReceivedOn(event.target.value) }} /></FormField><FormField id="receipt-comment" label="Комментарий"><input value={comment} onChange={event => { postRetryId.current = null; setComment(event.target.value) }} /></FormField></div>
      <FormField id="receipt-part-search" label="Найти запчасть для поставки"><input type="search" value={partQuery} onChange={event => setPartQuery(event.target.value)} /></FormField>{parts.length ? <ul className="inventory-picker-results">{parts.map(part => <li key={part.id}><span>{part.name} · <strong>{part.article}</strong></span><Button aria-label={`Добавить ${part.article}`} onClick={() => addLine(part)} size="compact" variant="secondary">Добавить</Button></li>)}</ul> : null}
      <div className="inventory-document-lines">{editorLines.map((line, index) => <div className="inventory-document-line" key={line.part.id}><strong>{line.part.name} · {line.part.article}</strong><FormField error={touchedLines.has(line.part.id) ? inventoryQuantityError(line.quantity) || (line.quantity === '0' ? 'Больше нуля' : undefined) : undefined} id={`receipt-quantity-${line.part.id}`} label={`Количество ${line.part.article}`}><input inputMode="numeric" value={line.quantity} onBlur={() => setTouchedLines(current => new Set(current).add(line.part.id))} onChange={event => { postRetryId.current = null; setLines(current => current.map((item, itemIndex) => itemIndex === index ? { ...item, quantity: event.target.value } : item)) }} /></FormField><Button aria-label={`Удалить ${line.part.article}`} onClick={() => { postRetryId.current = null; setLines(lines.filter((_, itemIndex) => itemIndex !== index)) }} size="compact" variant="ghost">Удалить</Button></div>)}</div><div className="inventory-document-actions"><Button busy={busy} disabled={invalid} onClick={saveDraft} variant="secondary">Сохранить черновик</Button>{canPost ? <Button disabled={invalid || busy} onClick={() => setAction('post')}>Провести поставку</Button> : null}{selected ? <Button disabled={busy} onClick={() => setAction('cancel')} variant="danger">Отменить черновик</Button> : null}</div></> : <><dl className="inventory-document-summary"><dt>Статус</dt><dd>{selected ? statusLabel[selected.status] : ''}</dd><dt>Позиций</dt><dd>{selected?.lines.length}</dd></dl>{selected ? <div className="inventory-document-lines">{selected.lines.map(line => <div className="inventory-document-line" key={line.id}><strong>{line.catalog_part_name} · {line.catalog_part_article}</strong><span>{line.catalog_component_name}</span><span>Количество: {line.quantity}</span></div>)}</div> : null}{selected?.status === 'draft' && canPost ? <Button disabled={busy} onClick={() => setAction('post')}>Провести поставку</Button> : null}{selected?.status === 'posted' && canPost ? <><FormField id="receipt-reverse-reason" label="Причина сторно"><input value={reverseReason} onChange={event => setReverseReason(event.target.value)} /></FormField><Button disabled={!reverseReason.trim() || busy} onClick={() => setAction('reverse')} variant="danger">Сторнировать</Button></> : null}</>}
    </div> : !mobile ? <div className="inventory-document-empty"><p>Выберите поставку или создайте новую.</p></div> : null}
    <ConfirmDialog confirmLabel={action === 'cancel' ? 'Подтвердить отмену' : action === 'reverse' ? 'Подтвердить сторно' : 'Подтвердить проведение'} description="Операция изменит остатки или статус документа." onConfirm={commitAction} onOpenChange={open => { if (!open && !busy) { if (action === 'reverse') setReverseReason(''); setAction(null) } }} open={action !== null} pending={busy} title="Подтвердите действие" tone={action === 'post' ? 'default' : 'danger'} />
  </section>
}
