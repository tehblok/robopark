import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api, ApiError, type DiagnosticRule } from '../../api'
import { Tabs } from '../../components/ui/Tabs'
import { adminResourceOptions } from '../../components/admin/adminResources'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { MasterDetail } from '../../design-system/layout/MasterDetail'
import { resourceStore, useCachedResource } from '../../lib/resource'
import { RuleForm } from './DiagnosticRuleEditor'
import { emptyDraft, errorText, type Draft } from './diagnosticRuleDraft'
import { unknownDiagnosticApi, type UnknownDiagnostic, type UnknownDiagnosticState } from './unknownDiagnosticApi'

const states = [{ id: 'new', label: 'Новые' }, { id: 'mapped', label: 'Размеченные' }, { id: 'ignored', label: 'Игнорируемые' }]
const date = (value: string) => new Date(value).toLocaleString('ru-RU')
const noop = () => undefined

export function UnknownDiagnosticInbox({ cachePrefix, active, onAccess, onRuleCreated, onOpenRule, previewOwner, isPreviewOwner }: {
  cachePrefix: string; active: boolean; onAccess: (error: unknown) => void
  onRuleCreated: (rule: DiagnosticRule) => Promise<void>; onOpenRule: (id: number, draft?: Draft) => void
  previewOwner: object; isPreviewOwner: (owner: object) => boolean
}) {
  const [state, setState] = useState<UnknownDiagnosticState>('new')
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<UnknownDiagnostic | null>(null)
  const [editing, setEditing] = useState<UnknownDiagnostic | null>(null)
  const [committed, setCommitted] = useState<{ unknownId: number; rule: DiagnosticRule } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const alive = useRef(false)
  const mutation = useRef(false)
  const denied = useRef(false)
  const activeRef = useRef(active)
  const selectionRef = useRef(selected?.id)
  useLayoutEffect(() => { activeRef.current = active; selectionRef.current = selected?.id }, [active, selected?.id])
  const key = `${cachePrefix}${state}:${offset}`
  const requestOwner = useRef<{ controller: AbortController; pending: number } | null>(null)
  useEffect(() => {
    alive.current = true
    const owner = { controller: new AbortController(), pending: 0 }
    requestOwner.current = owner
    return () => {
      alive.current = false
      if (owner.pending > 0) resourceStore.invalidate(key)
      owner.controller.abort()
    }
  }, [key])
  const access = (failure: unknown) => {
    if (!alive.current) return
    if (failure instanceof ApiError && [401, 403].includes(failure.status)) {
      denied.current = true
      resourceStore.invalidate(cachePrefix, { prefix: true })
      setSelected(null)
      onAccess(failure)
    }
  }
  const resource = useCachedResource(key, async () => {
    const owner = requestOwner.current
    if (!owner || !alive.current || denied.current) throw new DOMException('Retired inbox owner', 'AbortError')
    const signal = owner.controller.signal
    if (signal.aborted) throw new DOMException('Retired inbox owner', 'AbortError')
    owner.pending++
    try {
      const page = await unknownDiagnosticApi.list(state, offset, signal)
      if (signal?.aborted || !alive.current || denied.current) throw new DOMException('Retired inbox request', 'AbortError')
      return page
    } catch (failure) {
      if (!signal?.aborted) access(failure)
      throw failure
    } finally { owner.pending-- }
  }, { ...adminResourceOptions, enabled: active })
  useEffect(() => {
    if (resource.data) setSelected(current => current ? resource.data!.items.find(item => item.id === current.id) ?? current : null)
  }, [resource.data])
  const recoverMapped = async (failure: unknown, id: number) => {
    if (!(failure instanceof ApiError) || failure.detail !== 'diagnostic_unknown_already_mapped') return
    try {
      const latest = await unknownDiagnosticApi.get(id)
      if (alive.current && !denied.current && selectionRef.current === id) setSelected(latest)
      if (alive.current && !denied.current) { resourceStore.invalidate(cachePrefix, { prefix: true }); await resource.refresh() }
    } catch (readFailure) { access(readFailure) }
  }
  const updateState = async (item: UnknownDiagnostic) => {
    if (mutation.current || !alive.current || denied.current) return
    mutation.current = true; setBusy(true); setError(''); setNotice('')
    try {
      const result = await (item.state === 'ignored' ? unknownDiagnosticApi.reopen(item.id) : unknownDiagnosticApi.ignore(item.id))
      if (!alive.current || denied.current) return
      if (selectionRef.current === item.id) setSelected(current => current ? { ...current, state: result.state, rule_id: null } : current)
      resourceStore.invalidate(cachePrefix, { prefix: true })
      await resource.refresh()
      if (alive.current && !denied.current) setNotice(result.state === 'ignored' ? 'Ошибка скрыта из проверок робота до восстановления.' : 'Ошибка возвращена в проверки робота.')
    } catch (failure) { if (alive.current) { access(failure); setError(errorText(failure)); if (!denied.current) await recoverMapped(failure, item.id) } }
    finally { mutation.current = false; if (alive.current) setBusy(false) }
  }
  const classify = async (draft: Draft, id?: number, disable = false) => {
    if (!selected || mutation.current || !alive.current || denied.current) throw new Error('operation_unavailable')
    if (selected.state === 'mapped' && !id) throw new ApiError(409, 'diagnostic_unknown_already_mapped')
    mutation.current = true; setBusy(true); setError('')
    try {
      const result = id ? await (disable ? api.disableDiagnosticRule(id) : api.updateDiagnosticRule(id, draft)) : await unknownDiagnosticApi.classify(selected.id, draft)
      if (!alive.current || denied.current) return result
      setCommitted({ unknownId: selected.id, rule: result })
      resourceStore.invalidate(cachePrefix, { prefix: true })
      // Keep a committed identity even when catalogue revalidation fails.
      if (selectionRef.current === selected.id) setSelected(current => current ? { ...current, state: 'mapped', rule_id: result.id } : null)
      await onRuleCreated(result)
      await resource.refresh()
      return result
    } catch (failure) {
      if (alive.current && !denied.current) {
        access(failure)
        if (!denied.current) await recoverMapped(failure, selected.id)
      }
      throw failure
    }
    finally { mutation.current = false; if (alive.current) setBusy(false) }
  }
  const selectedId = selected?.id
  return <div className="rp-diagnostic-panel">
    <p className="rp-diagnostic-hint">Здесь собраны нераспознанные значения со всех роботов. Одинаковые источник и значение объединены. Наблюдения учитываются не чаще раза в минуту для каждой пары робот–ошибка. Разметьте ошибку, чтобы добавить расшифровку и место на роботе.</p>
    <Tabs items={states} value={state} onChange={value => { setState(value as UnknownDiagnosticState); setOffset(0) }} />
    {error || resource.error ? <ErrorState title="Не удалось загрузить неизвестные ошибки" description={error || errorText(resource.error)} onRetry={() => { setError(''); void resource.refresh() }} /> : null}
    {notice ? <p role="status">{notice}</p> : null}
    {resource.isLoading ? <LoadingState label="Загрузка неизвестных ошибок" variant="inline" /> : null}
    {editing && selected?.state === 'mapped' && selected.rule_id && committed?.unknownId !== selected.id ? <div className="rp-diagnostic-panel" role="status"><p>Эту ошибку уже разметили. Черновик сохранён на экране; откройте существующее правило, чтобы продолжить.</p><Button variant="secondary" onClick={() => onOpenRule(selected.rule_id!)}>Открыть правило №{selected.rule_id}</Button></div> : null}
    <MasterDetail detailOpen={Boolean(selected)} onBack={() => { setSelected(null); setEditing(null) }} list={<>
      <h2>Неизвестные ошибки</h2>
      {resource.data?.items.length === 0 ? <EmptyState title="Ошибок в этой группе нет" description="Новые неизвестные значения появятся здесь автоматически." /> : null}
      <ol className="rp-diagnostic-list">{resource.data?.items.map(item => <li key={item.id}>
        <Button type="button" variant="ghost" className="rp-diagnostic-select" aria-pressed={selectedId === item.id} onClick={() => { if (selectedId !== item.id) { setSelected(item); setEditing(null); setError('') } }}>
          <strong className="rp-diagnostic-unknown-pattern">{item.pattern}</strong><span>{item.source_path}</span>
          <span>Наблюдений: {item.observations} · Последний робот: {item.last_robot}</span>
          <span>Впервые: {date(item.first_seen_at)} · Последний раз: {date(item.last_seen_at)}</span>
        </Button>
      </li>)}</ol>
      {resource.data ? <nav className="rp-diagnostic-pagination" aria-label="Страницы неизвестных ошибок">
        <Button variant="secondary" disabled={offset === 0 || resource.isRevalidating} onClick={() => setOffset(Math.max(0, offset - 50))}>Назад</Button>
        <span>{resource.data.total ? `${offset + 1}–${offset + resource.data.items.length} из ${resource.data.total}` : '0 ошибок'}</span>
        <Button variant="secondary" disabled={!resource.data.has_more || resource.isRevalidating} onClick={() => setOffset(offset + 50)}>Далее</Button>
      </nav> : null}
    </>} detail={editing ? <RuleForm key={editing.id} rule={committed?.unknownId === editing.id ? committed.rule : undefined} initialDraft={{ ...emptyDraft, source_path: editing.source_path, pattern: editing.pattern, example: JSON.stringify(editing.original_value), x: NaN, y: NaN }} onDraftAdopted={noop}
      busy={busy} onSave={classify} onAccess={access} requirePlacement lockSource
      previewOwner={previewOwner} isPreviewOwner={isPreviewOwner}
      isCurrentNavigation={() => alive.current && !denied.current && activeRef.current && selectionRef.current === editing.id}
      onCreated={(id, draft) => { setEditing(null); onOpenRule(id, draft) }} /> : selected ? <section className="rp-diagnostic-panel" aria-label="Выбранная неизвестная ошибка">
      <h2>{selected.pattern}</h2><p>Источник: {selected.source_path}</p>
      <pre className="rp-diagnostic-raw">{JSON.stringify(selected.raw_value, null, 2)}</pre>
      <p>Наблюдений: {selected.observations} · Последний робот: {selected.last_robot}</p>
      <p>Впервые: {date(selected.first_seen_at)}<br />Последний раз: {date(selected.last_seen_at)}</p>
      {selected.state === 'mapped' && selected.rule_id ? <Button variant="secondary" onClick={() => onOpenRule(selected.rule_id!)}>Открыть правило №{selected.rule_id}</Button> : <>
        <Button disabled={busy || selected.original_value === null} onClick={() => setEditing(selected)}>Разметить</Button>
        {selected.original_value === null ? <p role="status">Повторите проверку робота для получения исходного сигнала.</p> : null}
        <p className="rp-diagnostic-hint">Игнорируемая ошибка скрыта из проверок робота до восстановления.</p>
        <Button variant="secondary" busy={busy} onClick={() => void updateState(selected)}>{selected.state === 'ignored' ? 'Вернуть' : 'Игнорировать'}</Button>
      </>}
    </section> : <EmptyState title="Выберите неизвестную ошибку" description="Откройте значение, чтобы разметить его или отложить." />} />
  </div>
}
