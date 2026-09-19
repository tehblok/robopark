import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, type InventoryCatalogSearchItem, type InventoryPageEnvelope, type InventoryReceipt, type InventoryReceiptInput } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, LoadingState } from '../../design-system/feedback/AsyncState'
import { FormField } from '../../design-system/forms/FormField'
import { ConfirmDialog } from '../../design-system/overlays/ConfirmDialog'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { classifyApiError } from '../../shared/api/classifyApiError'
import { inventoryQuantityError, isPositiveInventoryQuantity } from './inventoryTypes'
import './inventory.css'
import { InventorySecondaryActions } from './InventorySecondaryActions'

type ReceiptsApi = Pick<typeof api, 'inventoryReceipts' | 'searchInventory' | 'createInventoryReceipt' | 'updateInventoryReceipt' | 'postInventoryReceipt' | 'cancelInventoryReceipt' | 'reverseInventoryReceipt'>
type DraftLine = { part: InventoryCatalogSearchItem; quantity: string; note: string }
type Action = 'post' | 'cancel' | 'reverse' | null
const pageSize = 25
const today = () => new Date().toISOString().slice(0, 10)
const statusLabel = { draft: 'Черновик', posted: 'Проведена', cancelled: 'Отменена' } as const
const normalizePayload = (input: InventoryReceiptInput): InventoryReceiptInput => ({
  supplier: input.supplier?.trim() || null,
  document_number: input.document_number?.trim() || null,
  received_on: input.received_on,
  comment: input.comment?.trim() || null,
  lines: input.lines.map(line => ({
    catalog_part_id: line.catalog_part_id,
    quantity: (isPositiveInventoryQuantity(line.quantity) ? BigInt(line.quantity).toString() : line.quantity) as `${bigint}`,
    note: line.note?.trim() || null,
  })),
})
const comparisonSnapshot = (input: InventoryReceiptInput): string => {
  const normalized = normalizePayload(input)
  normalized.lines.sort((left, right) => {
    const leftId = BigInt(left.catalog_part_id)
    const rightId = BigInt(right.catalog_part_id)
    if (leftId !== rightId) return leftId < rightId ? -1 : 1
    if (left.quantity !== right.quantity) return left.quantity < right.quantity ? -1 : 1
    const leftNote = left.note ?? ''
    const rightNote = right.note ?? ''
    return leftNote === rightNote ? 0 : leftNote < rightNote ? -1 : 1
  })
  return JSON.stringify(normalized)
}

export function InventoryReceiptsView({ apiClient = api, parkId, onInventoryChanged, permissions, refreshVersion = 0 }: { apiClient?: ReceiptsApi; parkId: number; onInventoryChanged?: () => void; permissions?: string[]; refreshVersion?: number }) {
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
  const [baselinePayload, setBaselinePayload] = useState<string | null>(null)
  const [conflicted, setConflicted] = useState(false)
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
  const comparedPage = useRef<InventoryPageEnvelope<InventoryReceipt> | null>(null)
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

  useEffect(() => { load(); return () => { listGeneration.current += 1 } }, [load, refreshVersion])
  useEffect(() => {
    setPage(null); setSelected(null); setCreating(false); setLines([]); setTouchedLines(new Set()); setOffset(0); setNotice(''); setError('')
    operationGeneration.current += 1; partGeneration.current += 1; pending.current = false; setBaselinePayload(null); setReverseReason(''); setConflicted(false)
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

  const acceptReceipt = (receipt: InventoryReceipt, known: DraftLine[] = []) => {
    setSelected(receipt); setCreating(false); setSupplier(receipt.supplier ?? ''); setDocumentNumber(receipt.document_number ?? ''); setReceivedOn(receipt.received_on); setComment(receipt.comment ?? ''); setLines(receiptLines(receipt, known))
    setConflicted(false)
    // A serialized snapshot cannot change when the editor or a refreshed list changes.
    setBaselinePayload(comparisonSnapshot(receipt))
  }

  const begin = () => {
    setCreating(true); setSelected(null); setSupplier(''); setDocumentNumber(''); setReceivedOn(today()); setComment(''); setLines([]); setTouchedLines(new Set()); setPartQuery(''); setParts([]); setError(''); setNotice(''); setReverseReason(''); setBaselinePayload(null); setConflicted(false)
  }
  const open = (receipt: InventoryReceipt) => {
    acceptReceipt(receipt); setTouchedLines(new Set()); setError(''); setNotice(''); setReverseReason('')
  }
  const addLine = (part: InventoryCatalogSearchItem) => { setLines(current => {
    const existing = current.find(line => line.part.id === part.id)
    if (!existing) return [...current, { part, quantity: '1', note: '' }]
    return current.map(line => line.part.id === part.id ? { ...line, quantity: (BigInt(line.quantity || '0') + 1n).toString() } : line)
  }) }
  const draftLines = lines
  const invalid = !receivedOn || !draftLines.length || draftLines.some(line => !isPositiveInventoryQuantity(line.quantity))
  const payload = normalizePayload({ supplier, document_number: documentNumber, received_on: receivedOn, comment, lines: draftLines.map(line => ({ catalog_part_id: line.part.id, quantity: line.quantity as `${bigint}`, note: line.note })) })
  const dirty = comparisonSnapshot(payload) !== baselinePayload
  useEffect(() => {
    if (page === comparedPage.current) return
    comparedPage.current = page
    if (!selected || !page) return
    const latest = page.items.find(item => item.id === selected.id)
    if (!latest || latest.revision === selected.revision) return
    if (dirty || action) { setAction(null); setConflicted(true) }
    else acceptReceipt(latest)
  }, [page, selected, dirty, action])
  const receiptConflict = (reason: unknown) => {
    if (!(reason instanceof ApiError) || reason.status !== 409) return false
    const detail = reason.structuredDetail
    return detail != null && !Array.isArray(detail) && detail.code === 'inventory_receipt_stale'
  }
  const saveDraft = async () => {
    if (invalid || conflicted || pending.current || !canManage) return
    pending.current = true; setBusy(true); setError('')
    const generation = ++operationGeneration.current
    const requestedPark = parkId
    const requestedId = selected?.id ?? null
    try {
      const value = requestedId
        ? await apiClient.updateInventoryReceipt(requestedPark, requestedId, { ...payload, revision: selected!.revision })
        : await apiClient.createInventoryReceipt(requestedPark, payload)
      if (generation !== operationGeneration.current || value.park_id !== requestedPark) return
      acceptReceipt(value, draftLines); setNotice('Черновик сохранён'); setOffset(0); load()
    } catch (reason) {
      if (generation === operationGeneration.current) {
        if (receiptConflict(reason)) setConflicted(true)
        else setError(classifyApiError(reason, 'Не удалось сохранить черновик.').description)
      }
    } finally {
      if (generation === operationGeneration.current) { setBusy(false); pending.current = false }
    }
  }
  const commitAction = async () => {
    if (!action || conflicted || pending.current) return
    pending.current = true; setBusy(true); setError('')
    const generation = ++operationGeneration.current
    const requestedPark = parkId
    const requestedId = selected?.id ?? null
    try {
      let value: InventoryReceipt
      if (action === 'post') {
        if (invalid) return
        const saved = canManage && (creating || dirty)
          ? requestedId
            ? await apiClient.updateInventoryReceipt(requestedPark, requestedId, { ...payload, revision: selected!.revision })
            : await apiClient.createInventoryReceipt(requestedPark, payload)
          : selected!
        if (generation !== operationGeneration.current || saved.park_id !== requestedPark) return
        acceptReceipt(saved, draftLines)
        value = await apiClient.postInventoryReceipt(requestedPark, saved.id, saved.revision)
      } else if (action === 'cancel' && requestedId) value = await apiClient.cancelInventoryReceipt(requestedPark, requestedId, selected!.revision)
      else if (action === 'reverse' && requestedId) value = await apiClient.reverseInventoryReceipt(requestedPark, requestedId, reverseReason.trim())
      else return
      if (generation !== operationGeneration.current || value.park_id !== requestedPark) return
      acceptReceipt(value, draftLines); setAction(null); setReverseReason(''); setNotice(action === 'post' ? 'Поставка проведена' : action === 'cancel' ? 'Поставка отменена' : 'Поставка сторнирована')
      onInventoryChanged?.(); setOffset(0); load()
    } catch (reason) {
      if (generation === operationGeneration.current) { setAction(null); if (receiptConflict(reason)) setConflicted(true); else setError(classifyApiError(reason, 'Не удалось изменить поставку.').description) }
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
    {editorOpen ? <div className="inventory-document-editor"><Button className="inventory-document-back" disabled={busy} onClick={() => { setCreating(false); setSelected(null); setReverseReason(''); setBaselinePayload(null) }} variant="ghost">Назад к поставкам</Button><h2>{creating ? 'Новая поставка' : `Поставка №${selected?.id}`}</h2>
      {notice ? <p className="inventory-notice" role="status">{notice}</p> : null}{conflicted ? <div><p className="form-error" role="alert">Поставка изменена другим пользователем. Обновите поставку перед повторным редактированием; несохранённые правки будут отброшены.</p><Button onClick={() => { setSelected(null); setPage(null); setConflicted(false); setError(''); load() }} size="compact" variant="secondary">Обновить поставку</Button></div> : error ? <p className="form-error" role="alert">{error}</p> : null}
      {(creating || selected?.status === 'draft') && canManage ? <><div className="inventory-document-fields"><FormField id="receipt-supplier" label="Поставщик или завод"><input value={supplier} onChange={event => { setSupplier(event.target.value) }} /></FormField><FormField id="receipt-number" label="Номер документа"><input value={documentNumber} onChange={event => { setDocumentNumber(event.target.value) }} /></FormField><FormField id="receipt-date" label="Дата поставки"><input type="date" value={receivedOn} onChange={event => { setReceivedOn(event.target.value) }} /></FormField><FormField id="receipt-comment" label="Комментарий"><input value={comment} onChange={event => { setComment(event.target.value) }} /></FormField></div>
      <FormField id="receipt-part-search" label="Найти запчасть для поставки"><input type="search" value={partQuery} onChange={event => setPartQuery(event.target.value)} /></FormField>{parts.length ? <ul className="inventory-picker-results">{parts.map(part => <li key={part.id}><span>{part.name} · <strong>{part.article}</strong></span><Button aria-label={`Добавить ${part.article}`} onClick={() => addLine(part)} size="compact" variant="secondary">Добавить</Button></li>)}</ul> : null}
      <div className="inventory-document-lines">{editorLines.map((line, index) => <div className="inventory-document-line" key={line.part.id}><strong>{line.part.name} · {line.part.article}</strong><FormField error={touchedLines.has(line.part.id) ? inventoryQuantityError(line.quantity) || (line.quantity === '0' ? 'Больше нуля' : undefined) : undefined} id={`receipt-quantity-${line.part.id}`} label={`Количество ${line.part.article}`}><input inputMode="numeric" value={line.quantity} onBlur={() => setTouchedLines(current => new Set(current).add(line.part.id))} onChange={event => { setLines(current => current.map((item, itemIndex) => itemIndex === index ? { ...item, quantity: event.target.value } : item)) }} /></FormField><Button aria-label={`Удалить ${line.part.article}`} onClick={() => { setLines(lines.filter((_, itemIndex) => itemIndex !== index)) }} size="compact" variant="ghost">Удалить</Button></div>)}</div><div className="inventory-document-actions"><Button busy={busy} disabled={invalid || conflicted || (!creating && !dirty)} onClick={saveDraft} variant="secondary">Сохранить черновик</Button>{canPost ? <Button disabled={invalid || busy || conflicted} onClick={() => setAction('post')}>Провести поставку</Button> : null}{selected ? <InventorySecondaryActions><Button disabled={busy || conflicted} onClick={() => setAction('cancel')} variant="danger">Отменить черновик</Button></InventorySecondaryActions> : null}</div></> : <><dl className="inventory-document-summary"><dt>Статус</dt><dd>{selected ? statusLabel[selected.status] : ''}</dd><dt>Позиций</dt><dd>{selected?.lines.length}</dd></dl>{selected ? <div className="inventory-document-lines">{selected.lines.map(line => <div className="inventory-document-line" key={line.id}><strong>{line.catalog_part_name} · {line.catalog_part_article}</strong><span>{line.catalog_component_name}</span><span>Количество: {line.quantity}</span></div>)}</div> : null}{selected?.status === 'draft' && canPost ? <Button disabled={busy || conflicted} onClick={() => setAction('post')}>Провести поставку</Button> : null}{selected?.status === 'posted' && canPost ? <><FormField id="receipt-reverse-reason" label="Причина сторно"><input value={reverseReason} onChange={event => setReverseReason(event.target.value)} /></FormField><Button disabled={!reverseReason.trim() || busy} onClick={() => setAction('reverse')} variant="danger">Сторнировать</Button></> : null}</>}
    </div> : !mobile ? <div className="inventory-document-empty"><p>Выберите поставку или создайте новую.</p></div> : null}
    <ConfirmDialog confirmLabel={action === 'cancel' ? 'Подтвердить отмену' : action === 'reverse' ? 'Подтвердить сторно' : 'Подтвердить проведение'} description="Операция изменит остатки или статус документа." onConfirm={commitAction} onOpenChange={open => { if (!open && !busy) { if (action === 'reverse') setReverseReason(''); setAction(null) } }} open={action !== null} pending={busy} title="Подтвердите действие" tone={action === 'post' ? 'default' : 'danger'} />
  </section>
}
