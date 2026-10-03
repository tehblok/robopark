import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type PointerEvent } from 'react'
import {
  api,
  ApiError,
  type EmergencyAdminSection,
  type EmergencyDiscoveredField,
  type EmergencyReading,
  type EmergencyReadingDraft,
  type JsonValue,
} from '../../api'
import { useAuth } from '../../auth-context'
import { Toggle } from '../../components/ui/Tabs'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import { FormField } from '../../design-system/forms/FormField'
import { MasterDetail } from '../../design-system/layout/MasterDetail'
import { ROBOT_PHOTOS } from '../robots/robotPhotos'
import './diagnostics.css'

const FORMATS = {
  number: 'Число',
  percent: 'Процент',
  distance: 'Расстояние',
  current: 'Ток',
  state: 'Состояние',
} as const

const DIRECTIONS = {
  auto: 'Авто',
  left: 'Слева',
  right: 'Справа',
  top: 'Сверху',
  bottom: 'Снизу',
} as const

const emptyDraft = (sectionId = ''): EmergencyReadingDraft => ({
  section_id: sectionId,
  path: '',
  label: '',
  display_kind: 'number',
  unit: null,
  precision: 0,
  enabled_path: null,
  no_data_values: [],
  warning_below: null,
  warning_above: null,
  critical_below: null,
  critical_above: null,
  view: 'front',
  x: 0.5,
  y: 0.5,
  label_direction: 'auto',
  is_enabled: true,
  sort_order: 0,
})

function toDraft(reading: EmergencyReading): EmergencyReadingDraft {
  const { id: _id, ...draft } = reading
  return draft
}

function readingError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return 'Сессия истекла. Войдите снова.'
    if (error.detail === 'emergency_cookie_invalid') return 'Проверьте подключение к Emergency.'
    if (error.status === 403) return 'Нет доступа к настройке показаний.'
    if (error.status === 409 || error.status === 428) return 'Каталог изменился. Обновите список и повторите действие.'
    if (error.detail === 'invalid_robot_number') return 'Проверьте номер робота.'
    if (error.detail === 'emergency_cookie_not_configured') return 'Сначала настройте подключение к Emergency.'
    if (error.status === 422) return 'Проверьте выбранное поле и настройки показания.'
  }
  return 'Не удалось выполнить запрос. Повторите попытку.'
}

function parseNoData(value: string): JsonValue[] | null {
  try {
    const values: JsonValue[] = JSON.parse(`[${value}]`)
    return values.length <= 32 && values.every(item => item === null || typeof item === 'string' || typeof item === 'boolean' || (typeof item === 'number' && Number.isFinite(item))) ? values : null
  } catch {
    return null
  }
}

function previewValue(draft: EmergencyReadingDraft, example: string): string {
  if (draft.display_kind === 'state') {
    if (example === 'true') return 'Включено'
    if (example === 'false') return 'Отключено'
    return example || 'Нет данных'
  }
  const number = Number(example)
  const value = Number.isFinite(number) ? number.toFixed(draft.precision) : example || 'Нет данных'
  const defaultUnit = draft.display_kind === 'percent' ? '%' : draft.display_kind === 'current' ? 'А' : ''
  return `${value}${draft.unit || defaultUnit ? ` ${draft.unit || defaultUnit}` : ''}`
}

export function ReadingCatalogEditor() {
  const { user } = useAuth()
  const [catalog, setCatalog] = useState<{ readings: EmergencyReading[]; etag: string | null } | null>(null)
  const [sections, setSections] = useState<EmergencyAdminSection[]>([])
  const [selected, setSelected] = useState<number | 'new' | null>(null)
  const [draft, setDraft] = useState<EmergencyReadingDraft>(() => emptyDraft())
  const [example, setExample] = useState('')
  const [vin, setVin] = useState('')
  const [discovered, setDiscovered] = useState<EmergencyDiscoveredField[]>([])
  const [searchedVin, setSearchedVin] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(false)
  const [discovering, setDiscovering] = useState(false)
  const [busy, setBusy] = useState(false)
  const [denied, setDenied] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [formKey, setFormKey] = useState(0)
  const mounted = useRef(true)
  const viewRevision = useRef(0)
  const draftOwner = useRef(0)
  const discoveryRevision = useRef(0)
  const discoveryController = useRef<AbortController | null>(null)

  const deny = useCallback((failure: unknown) => {
    if (failure instanceof ApiError && (failure.status === 401 || (failure.status === 403 && failure.detail !== 'emergency_cookie_invalid'))) {
      setDenied(true)
      setCatalog(null)
    }
  }, [])

  const reload = useCallback(async (signal?: AbortSignal, preserveError = false) => {
    setLoading(true)
    if (!preserveError) setError('')
    try {
      const [nextCatalog, nextSections] = await Promise.all([
        api.emergencyReadings(signal),
        api.adminEmergencySections(),
      ])
      if (signal?.aborted || !mounted.current) return
      setCatalog(nextCatalog)
      setSections(nextSections)
      setDenied(false)
    } catch (failure) {
      if (signal?.aborted || !mounted.current) return
      deny(failure)
      setError(readingError(failure))
    } finally {
      if (!signal?.aborted && mounted.current) setLoading(false)
    }
  }, [deny])

  useEffect(() => {
    if (!user || user.access_status !== 'approved' || (user.role !== 'admin' && user.role !== 'royal')) return
    mounted.current = true
    const controller = new AbortController()
    void reload(controller.signal)
    return () => { mounted.current = false; controller.abort(); discoveryController.current?.abort() }
  }, [reload, user])

  if (!user || user.access_status !== 'approved' || (user.role !== 'admin' && user.role !== 'royal')) return null
  if (denied) return <ErrorState title="Каталог недоступен" description={error || 'Нет доступа к настройке показаний.'} />

  const edit = (changes: Partial<EmergencyReadingDraft>) => {
    viewRevision.current += 1
    setDraft(current => ({ ...current, ...changes }))
    setError('')
    setNotice('')
  }

  const editRaw = () => {
    viewRevision.current += 1
    setError('')
    setNotice('')
  }

  const selectReading = (reading: EmergencyReading) => {
    draftOwner.current += 1
    setFormKey(draftOwner.current)
    viewRevision.current += 1
    setSelected(reading.id)
    setDraft(toDraft(reading))
    setExample('')
    setError('')
    setNotice('')
  }

  const startNew = () => {
    draftOwner.current += 1
    setFormKey(draftOwner.current)
    viewRevision.current += 1
    setSelected('new')
    setDraft(emptyDraft(sections[0]?.id ?? ''))
    setExample('')
    setError('')
    setNotice('')
  }

  const selectField = (field: EmergencyDiscoveredField) => {
    draftOwner.current += 1
    setFormKey(draftOwner.current)
    const enabled = discovered.find(candidate => candidate.path === `${field.path}Enabled`)
    const leaf = field.path.split('.').at(-1)?.toLowerCase()
    viewRevision.current += 1
    setSelected('new')
    setDraft({
      ...emptyDraft(sections[0]?.id ?? ''),
      path: field.path,
      display_kind: field.value_type === 'number' ? 'number' : 'state',
      enabled_path: enabled?.path ?? null,
      no_data_values: ['lt', 'rt', 'lb', 'rb'].includes(leaf ?? '') ? [2147483647] : [],
    })
    setExample(field.example)
    setError('')
    setNotice('')
  }

  const discover = async () => {
    if (!vin.trim() || discovering) return
    const requestedVin = vin.trim()
    const revision = ++discoveryRevision.current
    const controller = new AbortController()
    discoveryController.current?.abort()
    discoveryController.current = controller
    setDiscovering(true)
    setDiscovered([])
    setSearchedVin(null)
    setError('')
    setNotice('')
    try {
      const fields = await api.discoverEmergencyReadings(requestedVin, controller.signal)
      if (mounted.current && discoveryRevision.current === revision) {
        setDiscovered(fields)
        setSearchedVin(requestedVin)
      }
    } catch (failure) {
      if (mounted.current && discoveryRevision.current === revision) {
        deny(failure)
        setError(readingError(failure))
      }
    } finally {
      if (mounted.current && discoveryRevision.current === revision) setDiscovering(false)
      if (discoveryController.current === controller) discoveryController.current = null
    }
  }

  const save = async (event: FormEvent, noDataValues: JsonValue[]) => {
    event.preventDefault()
    if (!catalog || busy || !draft.path || !draft.label.trim() || !draft.section_id) return
    const operationRevision = viewRevision.current
    const operationOwner = draftOwner.current
    const operationDraft = { ...draft, no_data_values: noDataValues }
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const result = selected === 'new'
        ? await api.createEmergencyReading({
            ...operationDraft,
            label: operationDraft.label.trim(),
            sort_order: Math.max(-1, ...catalog.readings.map(item => item.sort_order)) + 1,
          })
        : await api.updateEmergencyReading(selected!, { ...operationDraft, label: operationDraft.label.trim() })
      if (!mounted.current) return
      const refreshed = await api.emergencyReadings().catch(() => null)
      if (!mounted.current) return
      setCatalog(current => {
        const base = refreshed ?? current
        if (!base) return current
        return {
          etag: refreshed?.etag ?? null,
          readings: base.readings.some(item => item.id === result.id)
            ? base.readings.map(item => item.id === result.id ? result : item)
            : [...base.readings, result],
        }
      })
      if (draftOwner.current !== operationOwner) return
      setSelected(result.id)
      if (viewRevision.current !== operationRevision) return
      setDraft(toDraft(result))
      setNotice('Показание сохранено.')
    } catch (failure) {
      if (!mounted.current || viewRevision.current !== operationRevision) return
      deny(failure)
      setError(readingError(failure))
    } finally {
      setBusy(false)
    }
  }

  const reorder = async (index: number, offset: -1 | 1) => {
    if (!catalog || busy) return
    const target = index + offset
    if (target < 0 || target >= catalog.readings.length) return
    if (!catalog.etag) {
      setError('Каталог изменился. Обновите список и повторите действие.')
      await reload()
      return
    }
    const next = [...catalog.readings]
    const operationRevision = viewRevision.current
    ;[next[index], next[target]] = [next[target], next[index]]
    setBusy(true)
    setError('')
    try {
      const result = await api.reorderEmergencyReadings(next.map(item => item.id), catalog.etag)
      if (!mounted.current) return
      setCatalog(result)
      if (viewRevision.current !== operationRevision) return
      setNotice('Порядок сохранён.')
    } catch (failure) {
      if (!mounted.current || viewRevision.current !== operationRevision) return
      deny(failure)
      setError(readingError(failure))
      if (failure instanceof ApiError && [409, 428].includes(failure.status)) await reload(undefined, true)
    } finally {
      setBusy(false)
    }
  }

  const disable = async () => {
    if (!catalog || typeof selected !== 'number' || busy) return
    const operationRevision = viewRevision.current
    setBusy(true)
    setError('')
    try {
      const result = await api.disableEmergencyReading(selected)
      if (!mounted.current) return
      setCatalog(current => current ? { etag: null, readings: current.readings.map(item => item.id === result.id ? result : item) } : current)
      if (viewRevision.current !== operationRevision) return
      setDraft(toDraft(result))
      setNotice('Показание отключено.')
    } catch (failure) {
      if (!mounted.current || viewRevision.current !== operationRevision) return
      deny(failure)
      setError(readingError(failure))
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!catalog || typeof selected !== 'number' || busy || !window.confirm(`Удалить показание «${draft.label}»?`)) return
    const operationRevision = viewRevision.current
    setBusy(true)
    setError('')
    try {
      await api.deleteEmergencyReading(selected)
      if (!mounted.current) return
      setCatalog(current => current ? { etag: null, readings: current.readings.filter(item => item.id !== selected) } : current)
      if (viewRevision.current !== operationRevision) return
      setSelected(null)
      setNotice('Показание удалено.')
    } catch (failure) {
      if (!mounted.current || viewRevision.current !== operationRevision) return
      deny(failure)
      setError(readingError(failure))
    } finally {
      setBusy(false)
    }
  }

  const selectedReading = typeof selected === 'number'
    ? catalog?.readings.find(item => item.id === selected)
    : null
  const filtered = discovered.filter(field => `${field.path} ${field.example}`.toLowerCase().includes(search.trim().toLowerCase()))

  return <div className="rp-diagnostic-editor rp-reading-editor">
    <p className="rp-diagnostic-hint">Показания общие для всех парков. Выберите поле из примера робота, затем задайте понятную подпись и место.</p>
    {loading ? <LoadingState label={catalog ? 'Обновление каталога' : 'Загрузка каталога'} variant="inline" /> : null}
    {error ? <ErrorState title="Не удалось обновить показания" description={error} /> : null}
    {notice ? <p role="status">{notice}</p> : null}
    {catalog ? <MasterDetail detailOpen={selected !== null} onBack={() => { draftOwner.current += 1; viewRevision.current += 1; setSelected(null) }} list={<>
      <div className="rp-diagnostic-list-heading">
        <h2>Каталог показаний</h2>
        <div className="rp-reading-heading-actions">
          <Button type="button" variant="ghost" size="compact" onClick={() => void reload()}>Обновить каталог</Button>
          <Button type="button" variant="secondary" size="compact" onClick={startNew}>Новое показание</Button>
        </div>
      </div>
      <section className="rp-reading-discovery" aria-label="Поиск поля в примере робота">
        <FormField id="reading-sample-vin" label="Номер робота для примера" hint="Нужен только для безопасного поиска доступных скалярных полей.">
          <input maxLength={64} value={vin} onChange={event => {
            discoveryRevision.current += 1
            discoveryController.current?.abort()
            discoveryController.current = null
            setVin(event.target.value)
            setDiscovered([])
            setSearchedVin(null)
            setDiscovering(false)
          }} placeholder="R-107" />
        </FormField>
        <Button type="button" variant="secondary" busy={discovering} disabled={!vin.trim()} onClick={() => void discover()}>Найти показания</Button>
        {discovered.length ? <>
          <FormField id="reading-discovery-search" label="Поиск">
            <input role="searchbox" aria-label="Поиск показаний" value={search} onChange={event => setSearch(event.target.value)} />
          </FormField>
          <ul className="rp-reading-discovery-list">{filtered.map(field => <li key={field.path}>
            <button type="button" aria-label={`Выбрать ${field.path}`} onClick={() => selectField(field)}>
              <strong>{field.path}</strong><span>{field.example}</span>
            </button>
          </li>)}</ul>
        </> : null}
        {searchedVin && !discovered.length ? <EmptyState title="Поля не найдены" description="В ответе этого робота нет поддерживаемых скалярных полей. Проверьте номер или попробуйте другого робота." /> : null}
      </section>
      {!catalog.readings.length ? <EmptyState title="Показаний пока нет" description="Найдите поле в примере робота и создайте первое показание." /> : <ol className="rp-diagnostic-list">
        {catalog.readings.map((item, index) => <li key={item.id} data-selected={selected === item.id}>
          <button type="button" className="rp-diagnostic-select" aria-label={`Открыть показание ${item.label}`} aria-pressed={selected === item.id} onClick={() => selectReading(item)}>
            <strong>{item.label}</strong><span>{FORMATS[item.display_kind as keyof typeof FORMATS] ?? item.display_kind} · {item.is_enabled ? 'Включено' : 'Отключено'}</span>
          </button>
          <div className="rp-diagnostic-order">
            <Button type="button" variant="ghost" size="compact" aria-label={`Выше: ${item.label}`} disabled={busy || index === 0} onClick={() => void reorder(index, -1)}>↑</Button>
            <Button type="button" variant="ghost" size="compact" aria-label={`Ниже: ${item.label}`} disabled={busy || index === catalog.readings.length - 1} onClick={() => void reorder(index, 1)}>↓</Button>
          </div>
        </li>)}
      </ol>}
    </>} detail={selected === 'new' || selectedReading ? <ReadingForm
      key={formKey}
      draft={draft}
      example={example}
      sections={sections}
      busy={busy}
      existing={Boolean(selectedReading)}
      onEdit={edit}
      onRawEdit={editRaw}
      onSave={save}
      onDisable={() => void disable()}
      onDelete={() => void remove()}
    /> : <EmptyState title="Выберите показание" description="Откройте одну запись или найдите новое поле в примере робота." />} /> : null}
  </div>
}

function ReadingForm({ draft, example, sections, busy, existing, onEdit, onRawEdit, onSave, onDisable, onDelete }: {
  draft: EmergencyReadingDraft
  example: string
  sections: EmergencyAdminSection[]
  busy: boolean
  existing: boolean
  onEdit: (changes: Partial<EmergencyReadingDraft>) => void
  onRawEdit: () => void
  onSave: (event: FormEvent, noDataValues: JsonValue[]) => void
  onDisable: () => void
  onDelete: () => void
}) {
  const formId = useId()
  const [noDataInput, setNoDataInput] = useState(() => draft.no_data_values.map(value => JSON.stringify(value)).join(', '))
  const [advanced, setAdvanced] = useState(false)
  const noDataValues = parseNoData(noDataInput)
  const numeric = ['number', 'percent', 'distance', 'current'].includes(draft.display_kind)
  const photo = ROBOT_PHOTOS.find(item => item.id === draft.view) ?? ROBOT_PHOTOS[0]
  const validCoordinates = [draft.x, draft.y].every(value => Number.isFinite(value) && value >= 0 && value <= 1)
  const place = (event: PointerEvent<HTMLImageElement>) => {
    if (event.button !== 0 && event.button !== -1) return
    const rect = event.currentTarget.getBoundingClientRect()
    if (!rect.width || !rect.height) return
    const clamp = (value: number) => Math.round(Math.max(0, Math.min(1, value)) * 10000) / 10000
    onEdit({ x: clamp((event.clientX - rect.left) / rect.width), y: clamp((event.clientY - rect.top) / rect.height) })
  }
  const valid = Boolean(draft.path && draft.label.trim() && draft.section_id && validCoordinates && noDataValues)

  return <form className="rp-diagnostic-form" onSubmit={event => { event.preventDefault(); if (noDataValues) onSave(event, noDataValues) }}>
    <h2>{existing ? 'Редактирование показания' : 'Новое показание'}</h2>
    <div className="rp-diagnostic-fields">
      <FormField id={`${formId}-reading-label`} label="Название показания" required><input maxLength={128} value={draft.label} onChange={event => onEdit({ label: event.target.value })} /></FormField>
      <FormField id={`${formId}-reading-path`} label="JSON-путь" hint="Путь выбран из безопасного примера и показан только для справки."><input readOnly value={draft.path} /></FormField>
      <FormField id={`${formId}-reading-format`} label="Формат"><select value={draft.display_kind} onChange={event => onEdit({ display_kind: event.target.value as EmergencyReadingDraft['display_kind'] })}>{Object.entries(FORMATS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></FormField>
      <FormField id={`${formId}-reading-section`} label="Диагностический блок"><select value={draft.section_id} onChange={event => onEdit({ section_id: event.target.value })}><option value="">Выберите блок</option>{sections.map(section => <option key={section.id} value={section.id}>{section.title}</option>)}</select></FormField>
      <FormField id={`${formId}-reading-unit`} label="Единица"><input maxLength={32} value={draft.unit ?? ''} onChange={event => onEdit({ unit: event.target.value || null })} /></FormField>
      <FormField id={`${formId}-reading-precision`} label="Знаков после запятой"><input type="number" min={0} max={4} value={draft.precision} onChange={event => onEdit({ precision: Number(event.target.value) })} /></FormField>
      <FormField id={`${formId}-reading-enabled-path`} label="Путь доступности" hint="Подставляется автоматически для соседнего поля Enabled."><input readOnly value={draft.enabled_path ?? ''} /></FormField>
      <FormField id={`${formId}-reading-no-data`} label="Нет показания" hint={'JSON-значения через запятую: null, "0", 0, false.'} error={noDataValues === null ? 'Только JSON-значения: строки в кавычках, числа, true, false или null (до 32).' : undefined}><input value={noDataInput} onChange={event => { setNoDataInput(event.target.value); onRawEdit() }} onBlur={() => { if (noDataValues) onEdit({ no_data_values: noDataValues }) }} /></FormField>
      <FormField id={`${formId}-reading-view`} label="Ракурс"><select value={draft.view} onChange={event => onEdit({ view: event.target.value as EmergencyReadingDraft['view'] })}>{ROBOT_PHOTOS.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></FormField>
      <FormField id={`${formId}-reading-direction`} label="Направление подписи"><select value={draft.label_direction} onChange={event => onEdit({ label_direction: event.target.value as EmergencyReadingDraft['label_direction'] })}>{Object.entries(DIRECTIONS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></FormField>
    </div>
    {numeric ? <>
      <Button type="button" variant="ghost" aria-expanded={advanced} aria-controls={`${formId}-thresholds`} onClick={() => setAdvanced(value => !value)}>Дополнительные настройки</Button>
      {advanced ? <div id={`${formId}-thresholds`} className="rp-diagnostic-fields">
        {([['warning_below', 'Предупреждение ниже'], ['warning_above', 'Предупреждение выше'], ['critical_below', 'Критично ниже'], ['critical_above', 'Критично выше']] as const).map(([key, label]) => <FormField key={key} id={`${formId}-${key}`} label={label}><input type="number" step="any" value={draft[key] ?? ''} onChange={event => onEdit({ [key]: event.target.value === '' ? null : Number(event.target.value) })} /></FormField>)}
      </div> : null}
    </> : null}
    <section className="rp-diagnostic-placement" aria-label="Расположение показания">
      <p className="rp-diagnostic-hint">Нажмите на робота, чтобы поставить точку показания.</p>
      <div className="rp-diagnostic-fields">
        {(['x', 'y'] as const).map(axis => <FormField key={axis} id={`${formId}-${axis}`} label={`Координата ${axis.toUpperCase()}`}><input type="number" min={0} max={1} step={0.01} value={draft[axis]} onChange={event => onEdit({ [axis]: Number(event.target.value) })} /></FormField>)}
      </div>
      <figure className="rp-diagnostic-figure">
        <div className="rp-diagnostic-photo" style={{ maxWidth: `${Math.min(420, 440 * photo.width / photo.height)}px` }}>
          <img src={photo.src} alt={photo.title} width={photo.width} height={photo.height} draggable={false} onPointerUp={place} />
          {validCoordinates ? <span role="img" aria-label={`Маркер: ${draft.label || 'показание'}`} className="rp-diagnostic-marker" data-severity="info" style={{ left: `${draft.x * 100}%`, top: `${draft.y * 100}%` }}>•</span> : null}
        </div>
        <figcaption>Иллюстрация модели · {photo.title}</figcaption>
      </figure>
    </section>
    <section className="rp-diagnostic-preview" role="region" aria-label="Предпросмотр показания">
      <strong>{draft.label || 'Название показания'}</strong>
      <span>{previewValue(draft, example)}</span>
    </section>
    <Toggle label="Показание включено" checked={draft.is_enabled} onChange={is_enabled => onEdit({ is_enabled })} />
    <div className="rp-reading-actions">
      <Button type="submit" busy={busy} disabled={!valid}>Сохранить показание</Button>
      {existing && draft.is_enabled ? <Button type="button" variant="secondary" disabled={busy} onClick={onDisable}>Отключить показание</Button> : null}
      {existing ? <Button type="button" variant="danger" disabled={busy} onClick={onDelete}>Удалить показание</Button> : null}
    </div>
  </form>
}
