import { useCallback, useEffect, useLayoutEffect, useId, useRef, useState, type FormEvent, type PointerEvent } from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'
import { api, ApiError, type DiagnosticCatalog, type DiagnosticPreview, type DiagnosticRule, type DiagnosticRuleCreate, type User } from '../../api'
import { useAuth } from '../../auth-context'
import { Tabs, Toggle } from '../../components/ui/Tabs'
import { Panel } from '../../components/PageShell'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { FormField } from '../../design-system/forms/FormField'
import { MasterDetail } from '../../design-system/layout/MasterDetail'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { ROBOT_PHOTOS } from '../robots/robotPhotos'
import { resourceStore, useCachedResource } from '../../lib/resource'
import { adminResourceOptions } from '../../components/admin/adminResources'
import { UnknownDiagnosticInbox } from './UnknownDiagnosticInbox'
import { testDiagnosticSamples, type DiagnosticSampleResult } from './diagnosticSampleApi'
import { DiagnosticSampleSummary } from './DiagnosticSampleSummary'
import './diagnostics.css'

const catalogOwners = new WeakMap<User, number>()
let nextCatalogOwner = 0
function catalogKey(user: User, park: string | null) {
  if (!catalogOwners.has(user)) catalogOwners.set(user, ++nextCatalogOwner)
  return `admin:diagnostic-rules:${catalogOwners.get(user)}:${park ?? ''}`
}

export type Draft = Required<Omit<DiagnosticRuleCreate, 'sort_order'>>
const SEVERITIES = { info: 'Информация', warning: 'Предупреждение', critical: 'Критическая ошибка' } as const
const INDICATORS = { point: 'Точка', outline: 'Контур', zone: 'Зона' } as const
export const emptyDraft: Draft = { source_path: '', match_kind: 'exact', pattern: '', example: '', title: '', description: '', severity: 'warning', part: '', preferred_view: 'front', x: .5, y: .5, indicator: 'point', is_enabled: true }
const isCoordinate = (value: number) => Number.isFinite(value) && value >= 0 && value <= 1
function toDraft(rule: DiagnosticRule): Draft {
  const { source_path, match_kind, pattern, example, title, description, severity, part, preferred_view, x, y, indicator, is_enabled } = rule
  return { source_path, match_kind, pattern, example, title, description, severity, part, preferred_view, x, y, indicator, is_enabled }
}
export function errorText(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 401) return 'Сессия истекла. Войдите снова.'
    if (error.status === 403) return 'Нет доступа к каталогу ошибок. Обратитесь к администратору.'
    if (error.detail === 'unsupported_diagnostic_regex') return 'Этот шаблон не поддерживается или слишком сложен. Упростите регулярное выражение.'
    if (error.detail === 'invalid_diagnostic_regex') return 'Некорректное регулярное выражение. Проверьте скобки и специальные символы.'
    if (error.detail === 'diagnostic_preview_source_too_large') return 'Пример требует слишком большого массива. Уменьшите индексы в пути источника.'
    if (error.detail === 'invalid_diagnostic_source_path') return 'Проверьте путь источника: используйте имена полей и индексы через точку.'
    if (error.detail === 'unknown_sample_requires_observation') return 'Повторите проверку робота для получения исходного сигнала.'
    if (error.detail === 'unknown_rule_does_not_match') return 'Правило не распознаёт исходный сигнал. Проверьте шаблон и повторите проверку примера.'
    if (error.detail === 'diagnostic_sample_catalog_too_large') return 'Каталог слишком велик для полной проверки пересечений. Допускается до 100 включённых правил.'
    if (error.status === 422) return 'Проверьте поля правила и пример: сервер не смог их обработать.'
    if (error.detail === 'diagnostic_unknown_already_mapped') return 'Эту ошибку уже разметили. Откройте связанное правило.'
    if (error.status === 409) return 'Правило конфликтует с каталогом. Обновите список и повторите сохранение.'
  }
  return 'Не удалось выполнить запрос. Повторите попытку.'
}

export function DiagnosticRuleEditor() {
  const { user } = useAuth()
  const [params] = useSearchParams()
  const park = params.get('park')
  const [owner, setOwner] = useState({ user, park, generation: 0 })
  // An auth object replacement is a new owner even if its numeric ID is reused.
  // Reset during render so protected drafts are never painted for that owner.
  if (owner.user !== user || owner.park !== park) {
    setOwner({ user, park, generation: owner.generation + 1 })
    return null
  }
  if (!user || user.access_status !== 'approved' || (user.role !== 'admin' && user.role !== 'royal')) return null
  return <DiagnosticCatalogEditor key={owner.generation} user={user} />
}

function DiagnosticCatalogEditor({ user }: { user: User }) {
  const { refreshUser } = useAuth()
  const [params, setParams] = useSearchParams()
  const location = useLocation()
  const navigation = useRef(location.key)
  useLayoutEffect(() => { navigation.current = location.key }, [location.key])
  const cacheKey = catalogKey(user, params.get('park'))
  const [section, setSection] = useState('catalog')
  const [inboxOpened, setInboxOpened] = useState(false)
  const [catalog, setCatalog] = useState<DiagnosticCatalog | null>(() => resourceStore.get<DiagnosticCatalog>(cacheKey) ?? null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [denied, setDenied] = useState(false)
  const [busy, setBusy] = useState(false)
  const [createdDraft, setCreatedDraft] = useState<{ id: number; draft: Draft; fromSelected?: string | null } | null>(null)
  const clearCreatedDraft = useCallback(() => setCreatedDraft(null), [])
  const [previewOwner, setPreviewOwner] = useState<object>({})
  const previewOwnerRef = useRef(previewOwner)
  const advancePreviewOwner = useCallback(() => {
    const next = {}
    previewOwnerRef.current = next
    setPreviewOwner(next)
  }, [])
  const isPreviewOwner = useCallback((owner: object) => previewOwnerRef.current === owner, [])
  const mutation = useRef(false)
  const alive = useRef(false)
  const listGeneration = useRef(0)
  const pendingLists = useRef(0)
  const refreshAuth = useRef(refreshUser)
  useLayoutEffect(() => { refreshAuth.current = refreshUser }, [refreshUser])
  const deniedRef = useRef(false)
  const validOwner = () => alive.current && !deniedRef.current
  const handleAccess = useCallback((failure: unknown) => {
    if (!(failure instanceof ApiError) || ![401, 403].includes(failure.status) || !alive.current) return
    deniedRef.current = true
    resourceStore.invalidate(`${cacheKey}:unknowns:`, { prefix: true })
    resourceStore.invalidate(cacheKey)
    setCatalog(null); setDenied(true); setError(errorText(failure)); setNotice('')
    if (failure.status === 401) void refreshAuth.current().catch(() => undefined)
  }, [cacheKey])
  const reload = useCallback(async (signal?: AbortSignal) => {
    // A queued focus callback may outlive the effect that disabled polling.
    // Retire it before any protected request, not only before applying its result.
    if (!alive.current || deniedRef.current || signal?.aborted) throw new DOMException('Retired catalog owner', 'AbortError')
    advancePreviewOwner()
    const generation = ++listGeneration.current
    pendingLists.current++
    setLoading(true)
    try {
      const next = await api.diagnosticRules(signal)
      if (alive.current && !deniedRef.current && generation === listGeneration.current) {
        advancePreviewOwner()
        setCatalog(next); resourceStore.set(cacheKey, next, false); setError('')
      }
      if (!alive.current || deniedRef.current || generation !== listGeneration.current) throw new DOMException('Retired catalog request', 'AbortError')
      return next
    } catch (failure) {
      if (alive.current && !signal?.aborted && generation === listGeneration.current) {
        handleAccess(failure); setError(errorText(failure))
      }
      throw failure
    } finally {
      pendingLists.current--
      if (alive.current && generation === listGeneration.current) setLoading(false)
    }
  }, [handleAccess, advancePreviewOwner, cacheKey])
  const catalogRequest = useRef<AbortController | null>(null)
  useEffect(() => {
    alive.current = true
    const controller = new AbortController()
    catalogRequest.current = controller
    return () => {
      alive.current = false
      // StrictMode may immediately mount again. Do not let that mount share a
      // request this owner is about to abort; completed cache entries survive.
      if (pendingLists.current > 0) resourceStore.invalidate(cacheKey)
      controller.abort()
    }
  }, [reload, user, cacheKey])
  useCachedResource(cacheKey, () => reload(catalogRequest.current?.signal), {
    ...adminResourceOptions,
    enabled: !denied,
  })

  const select = (id: string | null) => {
    if (params.get('rule') === id) return
    clearCreatedDraft()
    const next = new URLSearchParams(params)
    if (id) next.set('rule', id); else next.delete('rule')
    setParams(next)
  }
  const selected = params.get('rule')
  useEffect(() => {
    if (createdDraft && selected !== 'new' && selected !== String(createdDraft.id) && selected !== createdDraft.fromSelected) clearCreatedDraft()
  }, [createdDraft, selected, clearCreatedDraft])
  const rule = catalog?.rules.find(item => String(item.id) === selected)
  const reorder = async (index: number, offset: number) => {
    if (!catalog || mutation.current || !validOwner()) return
    advancePreviewOwner()
    mutation.current = true; setBusy(true); setNotice(''); setError('')
    try {
      if (!catalog.etag) throw new ApiError(428)
      const ids = catalog.rules.map(item => item.id)
      ;[ids[index], ids[index + offset]] = [ids[index + offset], ids[index]]
      await api.reorderDiagnosticRules(ids, catalog.etag)
      if (!validOwner()) return
      await reload()
      if (validOwner()) setNotice('Порядок сохранён.')
    } catch (failure) {
      if (!validOwner()) return
      handleAccess(failure)
      if (failure instanceof ApiError && [409, 428].includes(failure.status)) {
        try {
          await reload()
          if (validOwner()) setNotice('Каталог изменился. Список обновлён — повторите перемещение.')
        } catch { /* reload owns the visible retry error */ }
      } else setError(errorText(failure))
    } finally { mutation.current = false; if (alive.current) setBusy(false) }
  }
  const save = async (draft: Draft, id?: number, disable = false) => {
    if (mutation.current || !validOwner()) throw new Error('operation_unavailable')
    advancePreviewOwner()
    mutation.current = true; setBusy(true); setNotice('')
    try {
      const result = disable && id ? await api.disableDiagnosticRule(id) : id
        ? await api.updateDiagnosticRule(id, draft)
        : await api.createDiagnosticRule({ ...draft, sort_order: Math.max(-1, ...catalog!.rules.map(item => item.sort_order)) + 1 })
      if (validOwner()) {
        advancePreviewOwner()
        // The mutation is already committed. Keep its authoritative identity if
        // the subsequent catalog read fails, so Create cannot be retried twice.
        setCatalog(current => current ? {
          etag: null,
          rules: current.rules.some(item => item.id === result.id)
            ? current.rules.map(item => item.id === result.id ? result : item)
            : [...current.rules, result],
        } : current)
        await reload().catch(() => undefined)
      }
      return result
    } catch (failure) { if (validOwner()) handleAccess(failure); throw failure }
    finally { mutation.current = false; if (alive.current) setBusy(false) }
  }

  if (denied) return <ErrorState title="Каталог недоступен" description={error} />
  return <div className="rp-diagnostic-editor">
    <Tabs items={[{ id: 'catalog', label: 'Каталог ошибок' }, { id: 'unknowns', label: 'Неизвестные ошибки' }]} value={section} onChange={value => { advancePreviewOwner(); setSection(value); if (value === 'unknowns') setInboxOpened(true) }} />
    <div hidden={section !== 'catalog'} className="rp-diagnostic-panel" role="tabpanel" aria-label="Каталог ошибок">
    <p className="rp-diagnostic-hint">Правила действуют во всех парках. Выберите ошибку или создайте правило и укажите её место на изображении.</p>
    {error ? <ErrorState title="Не удалось обновить каталог" description={error} onRetry={() => { void reload().catch(() => undefined) }} /> : null}
    {notice ? <p role="status">{notice}</p> : null}
    {loading ? <LoadingState label={catalog ? 'Обновление каталога' : 'Загрузка каталога'} variant="inline" /> : null}
    {catalog ? <MasterDetail detailOpen={Boolean(selected)} onBack={() => select(null)} list={<>
      <div className="rp-diagnostic-list-heading"><h2>Каталог ошибок</h2><Button type="button" variant="secondary" onClick={() => select('new')}>Новое правило</Button></div>
      {!catalog.rules.length ? <EmptyState title="Правил пока нет" description="Создайте первое правило по примеру ошибки." /> : <ol className="rp-diagnostic-list">
        {catalog.rules.map((item, index) => <li key={item.id} data-selected={selected === String(item.id)}>
          <button type="button" className="rp-diagnostic-select" aria-label={`Открыть правило ${item.title}`} aria-pressed={selected === String(item.id)} onClick={() => select(String(item.id))}>
            <strong>{item.title}</strong>
            <StatusBadge tone={item.severity}>{SEVERITIES[item.severity]}</StatusBadge>
            <span>{item.is_enabled ? 'Включено' : 'Отключено'} · {ROBOT_PHOTOS.find(photo => photo.id === item.preferred_view)?.title}</span>
          </button>
          <div className="rp-diagnostic-order">
            <Button type="button" variant="ghost" aria-label={`Выше: ${item.title}`} disabled={busy || loading || index === 0} onClick={() => void reorder(index, -1)}>↑</Button>
            <Button type="button" variant="ghost" aria-label={`Ниже: ${item.title}`} disabled={busy || loading || index === catalog.rules.length - 1} onClick={() => void reorder(index, 1)}>↓</Button>
          </div>
        </li>)}
      </ol>}
    </>} detail={selected === 'new' || rule ? <RuleForm key={selected} rule={rule} busy={busy || loading} onSave={save} onAccess={handleAccess}
      previewOwner={previewOwner} isPreviewOwner={isPreviewOwner}
      initialDraft={createdDraft?.id === rule?.id ? createdDraft?.draft : undefined} onDraftAdopted={clearCreatedDraft}
      onCreated={(id, draft) => {
        setCreatedDraft({ id, draft })
        const next = new URLSearchParams(params); next.set('rule', String(id)); setParams(next, { replace: true })
      }} isCurrentNavigation={() => navigation.current === location.key} /> : <EmptyState title={selected ? 'Правило не найдено' : 'Выберите правило'} description={selected ? 'Возможно, каталог изменился. Выберите правило из списка.' : 'Откройте ошибку из каталога, чтобы настроить расшифровку и индикацию.'} />} /> : null}
    </div>
    {inboxOpened ? <div hidden={section !== 'unknowns'} className="rp-diagnostic-panel" role="tabpanel" aria-label="Неизвестные ошибки"><UnknownDiagnosticInbox cachePrefix={`${cacheKey}:unknowns:`} active={section === 'unknowns'} onAccess={handleAccess}
      previewOwner={previewOwner} isPreviewOwner={isPreviewOwner}
      onRuleCreated={async result => {
        if (!validOwner()) return
        setCatalog(current => current ? { etag: null, rules: [...current.rules.filter(item => item.id !== result.id), result] } : { etag: null, rules: [result] })
        await reload().catch(() => undefined)
      }}
      onOpenRule={(id, draft) => {
        if (draft) setCreatedDraft({ id, draft, fromSelected: selected })
        setSection('catalog')
        if (!catalog?.rules.some(item => item.id === id)) void reload().catch(() => undefined)
        const next = new URLSearchParams(params); next.set('rule', String(id)); setParams(next)
      }} /></div> : null}
  </div>
}

export function RuleForm({ rule, initialDraft, onDraftAdopted, busy, onSave, onCreated, onAccess, isCurrentNavigation, previewOwner, isPreviewOwner, requirePlacement = false, lockSource = false }: {
  rule?: DiagnosticRule; busy: boolean; requirePlacement?: boolean; lockSource?: boolean
  initialDraft?: Draft; onDraftAdopted: () => void
  onSave: (draft: Draft, id?: number, disable?: boolean) => Promise<DiagnosticRule>
  onCreated: (id: number, draft: Draft) => void; onAccess: (error: unknown) => void; isCurrentNavigation: () => boolean
  previewOwner: object; isPreviewOwner: (owner: object) => boolean
}) {
  const formId = useId()
  const [draft, setDraft] = useState<Draft>(() => initialDraft ?? (rule ? toDraft(rule) : { ...emptyDraft }))
  const latestDraft = useRef(draft)
  useEffect(() => { if (initialDraft) onDraftAdopted() }, [initialDraft, onDraftAdopted])
  const [preview, setPreview] = useState<DiagnosticPreview | null>(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [sampleResult, setSampleResult] = useState<DiagnosticSampleResult | null>(null)
  const [sampleBusy, setSampleBusy] = useState(false)
  const sampleRequest = useRef<AbortController | null>(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [failedImage, setFailedImage] = useState<string | null>(null)
  const revision = useRef(0)
  const mounted = useRef(false)
  const previewRequest = useRef<AbortController | null>(null)
  const previewGeneration = useRef(0)
  const invalidatePreview = useCallback(() => {
    previewGeneration.current++
    previewRequest.current?.abort()
    sampleRequest.current?.abort()
    setSampleBusy(false); setSampleResult(null)
    setPreviewBusy(false); setPreview(null)
  }, [])
  useLayoutEffect(invalidatePreview, [previewOwner, invalidatePreview])
  const retire = useCallback(() => { mounted.current = false; revision.current++; previewGeneration.current++; previewRequest.current?.abort(); sampleRequest.current?.abort() }, [])
  useEffect(() => { mounted.current = true; return retire }, [retire])
  const edit = (change: Partial<Draft>) => {
    revision.current++; invalidatePreview()
    latestDraft.current = { ...latestDraft.current, ...change }
    setDraft(latestDraft.current); setPreview(null); setError(''); setMessage('')
  }
  const coordinatesValid = [draft.x, draft.y].every(isCoordinate)
  const valid = coordinatesValid && draft.pattern.length <= 512 && ['source_path', 'pattern', 'example', 'title', 'description', 'part'].every(key => String(draft[key as keyof Draft]).trim())
  const photo = ROBOT_PHOTOS.find(item => item.id === draft.preferred_view)!
  const place = (event: PointerEvent<HTMLImageElement>) => {
    if (failedImage === photo.id || (event.button !== 0 && event.button !== -1)) return
    const rect = event.currentTarget.getBoundingClientRect()
    if (!rect.width || !rect.height) return
    const clamp = (value: number) => Math.round(Math.max(0, Math.min(1, value)) * 10000) / 10000
    edit({ x: clamp((event.clientX - rect.left) / rect.width), y: clamp((event.clientY - rect.top) / rect.height) })
  }
  const check = async () => {
    if (busy || previewBusy || !valid) return
    const current = revision.current
    const generation = ++previewGeneration.current
    const controller = new AbortController(); previewRequest.current = controller
    const ownsPreview = () => mounted.current && !controller.signal.aborted && current === revision.current
      && generation === previewGeneration.current && isPreviewOwner(previewOwner) && isCurrentNavigation()
    setPreviewBusy(true); setError(''); setPreview(null)
    try {
      const result = await api.previewDiagnosticRule(draft, undefined, controller.signal)
      if (ownsPreview()) setPreview(result)
    } catch (failure) {
      if (ownsPreview()) { onAccess(failure); setError(errorText(failure)) }
    } finally { if (ownsPreview()) setPreviewBusy(false) }
  }
  const checkSamples = async () => {
    if (busy || sampleBusy || !valid) return
    const current = revision.current
    const controller = new AbortController(); sampleRequest.current = controller
    const ownsResult = () => mounted.current && !controller.signal.aborted && current === revision.current
      && isPreviewOwner(previewOwner) && isCurrentNavigation()
    setSampleBusy(true); setSampleResult(null); setError('')
    try {
      const result = await testDiagnosticSamples(draft, rule?.id, controller.signal)
      if (ownsResult()) setSampleResult(result)
    } catch (failure) {
      if (ownsResult()) { onAccess(failure); setError(errorText(failure)) }
    } finally { if (ownsResult()) setSampleBusy(false) }
  }
  const save = async (event?: FormEvent, disable = false) => {
    event?.preventDefault()
    if (busy || (!disable && !valid)) return
    invalidatePreview()
    const current = revision.current
    setError(''); setMessage('')
    try {
      const result = await onSave(draft, rule?.id, disable)
      if (!mounted.current || !isCurrentNavigation()) return
      // A successful POST owns an identity even if local edits outlive its
      // request. Transfer that latest draft across the URL-keyed form remount.
      if (!rule) {
        onCreated(result.id, current === revision.current ? toDraft(result) : latestDraft.current)
        return
      }
      if (current !== revision.current) return
      latestDraft.current = toDraft(result)
      setDraft(latestDraft.current); setPreview(null)
      setMessage(disable ? 'Правило отключено.' : 'Правило сохранено.')
    } catch (failure) {
      if (mounted.current && current === revision.current) setError(errorText(failure))
    }
  }
  const title = rule ? 'Редактирование правила' : lockSource ? 'Разметка неизвестной ошибки' : 'Новое правило'
  return <Panel collapsible storageKey="diagnostics-rule-editor" title={title}>
    <form className="rp-diagnostic-form" onSubmit={event => void save(event)}>
    <div className="rp-diagnostic-fields">
      <FormField id={`${formId}-diagnostic-title`} label="Название ошибки" required><input maxLength={256} value={draft.title} onChange={event => edit({ title: event.target.value })} /></FormField>
      <FormField id={`${formId}-diagnostic-part`} label="Часть робота" required><input maxLength={128} value={draft.part} onChange={event => edit({ part: event.target.value })} /></FormField>
      <FormField id={`${formId}-diagnostic-source`} label="Путь источника" required hint="Поля и индексы через точку, например errors или data.errors.0"><input readOnly={lockSource} maxLength={256} value={draft.source_path} onChange={event => edit({ source_path: event.target.value })} /></FormField>
      <FormField id={`${formId}-diagnostic-kind`} label="Сопоставление"><select value={draft.match_kind} onChange={event => edit({ match_kind: event.target.value as Draft['match_kind'] })}><option value="exact">Точное совпадение</option><option value="regex">Регулярное выражение</option></select></FormField>
      <FormField id={`${formId}-diagnostic-pattern`} label={<span id={`${formId}-diagnostic-pattern-label`}>Очищенное тело ошибки</span>} required hint="Для обычного правила вставьте текст без уровня WARN, ERROR или CRIT и без прошедшего времени. Регулярное выражение остаётся расширенным режимом." error={draft.pattern.length > 512 ? 'Значение длиннее 512 символов. Выберите регулярное выражение и задайте короткий шаблон.' : undefined}><input aria-label="Код или шаблон" aria-labelledby={`${formId}-diagnostic-pattern-label`} maxLength={512} value={draft.pattern} onChange={event => edit({ pattern: event.target.value })} /></FormField>
      <FormField id={`${formId}-diagnostic-severity`} label="Уровень ошибки"><select value={draft.severity} onChange={event => edit({ severity: event.target.value as Draft['severity'] })}>{Object.entries(SEVERITIES).map(([id, title]) => <option key={id} value={id}>{title}</option>)}</select></FormField>
    </div>
    <FormField id={`${formId}-diagnostic-description`} label="Расшифровка" required><textarea rows={3} value={draft.description} onChange={event => edit({ description: event.target.value })} /></FormField>
    <section className="rp-diagnostic-placement" aria-label="Расположение ошибки">
      <h3>Место на роботе</h3>
      {requirePlacement ? <p className="rp-diagnostic-hint">Выберите ракурс, часть робота и укажите место ошибки. Локализация появится только после сохранения правила.</p> : null}
      <div className="rp-diagnostic-fields">
        <FormField id={`${formId}-diagnostic-view`} label="Ракурс"><select value={draft.preferred_view} onChange={event => edit({ preferred_view: event.target.value as Draft['preferred_view'] })}>{ROBOT_PHOTOS.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></FormField>
        <FormField id={`${formId}-diagnostic-indicator`} label="Индикация"><select value={draft.indicator} onChange={event => edit({ indicator: event.target.value as Draft['indicator'] })}>{Object.entries(INDICATORS).map(([id, title]) => <option key={id} value={id}>{title}</option>)}</select></FormField>
      </div>
      <p id={`${formId}-diagnostic-placement-hint`} className="rp-diagnostic-hint">Нажмите на изображение или задайте координаты от 0 до 1. Начало координат — верхний левый угол.</p>
      <figure className="rp-diagnostic-figure">
        <div className="rp-diagnostic-photo" style={{ maxWidth: `${Math.min(420, 440 * photo.width / photo.height)}px` }}>
          <img key={photo.id} src={photo.src} alt={photo.title} width={photo.width} height={photo.height} draggable={false} onPointerUp={place} onError={() => setFailedImage(photo.id)} />
          {failedImage !== photo.id && coordinatesValid ? <span role="img" aria-label={`Маркер: ${draft.part || 'часть робота'}`} className="rp-diagnostic-marker" data-severity={draft.severity} data-indicator={draft.indicator} style={{ left: `${draft.x * 100}%`, top: `${draft.y * 100}%` }}>!</span> : null}
        </div>
        <figcaption>Иллюстрация модели · {photo.title}</figcaption>
      </figure>
      {failedImage === photo.id ? <p role="alert">Изображение не загрузилось. Выберите другой ракурс или задайте координаты вручную.</p> : null}
      <div className="rp-diagnostic-fields">
        {(['x', 'y'] as const).map(axis => <FormField key={axis} id={`${formId}-diagnostic-${axis}`} label={`Координата ${axis.toUpperCase()}`} required error={isCoordinate(draft[axis]) ? undefined : 'Укажите число от 0 до 1.'}><input type="number" min={0} max={1} step="0.0001" aria-describedby={`${formId}-diagnostic-placement-hint`} value={Number.isNaN(draft[axis]) ? '' : draft[axis]} onChange={event => edit({ [axis]: event.target.value === '' ? NaN : Number(event.target.value) })} /></FormField>)}
      </div>
    </section>
    <FormField id={`${formId}-diagnostic-example`} label="Пример входного значения" required hint="Текст или JSON. Пример проверяется сервером по этому правилу."><textarea readOnly={lockSource} rows={3} value={draft.example} onChange={event => edit({ example: event.target.value })} /></FormField>
    <div><Button type="button" variant="secondary" disabled={busy || !valid} busy={previewBusy} onClick={() => void check()}>Проверить пример</Button></div>
    {preview ? <section className="rp-diagnostic-preview" aria-label="Результат проверки" role="status">
      <StatusBadge tone={preview.matched ? 'success' : 'warning'}>{preview.matched ? 'Совпадение найдено' : 'Совпадение не найдено'}</StatusBadge>
      {!preview.matched ? <p>Значение не распознано этим правилом. Проверьте код, путь и пример.</p> : null}
      {preview.events.map(item => <div key={item.id}><strong>{item.title}</strong><p>{item.description}</p><p>{item.part ?? 'Без локализации'} · {SEVERITIES[item.severity]}</p></div>)}
    </section> : null}
    <div><Button type="button" variant="secondary" disabled={busy || !valid} busy={sampleBusy} onClick={() => void checkSamples()}>Проверить собранные ошибки</Button></div>
    <p className="rp-diagnostic-hint">Проверка черновика по последним 50 сохранённым исходным сигналам всех парков, включая размеченные и игнорируемые. Правило публикуется только после сохранения.</p>
    {sampleResult ? <DiagnosticSampleSummary result={sampleResult} /> : null}
    {error ? <ErrorState title="Не удалось обработать правило" description={error} /> : null}
    {message ? <p role="status">{message}</p> : null}
    <Toggle label="Правило включено" checked={draft.is_enabled} onChange={is_enabled => edit({ is_enabled })} />
    <div className="rp-diagnostic-save"><Button type="submit" disabled={!valid} busy={busy}>Сохранить правило</Button></div>
    {rule?.is_enabled ? <div className="rp-diagnostic-disable"><p>Отключённое правило остаётся в каталоге и истории.</p><Button type="button" variant="secondary" disabled={busy} onClick={() => void save(undefined, true)}>Отключить правило</Button></div> : null}
    </form>
  </Panel>
}
