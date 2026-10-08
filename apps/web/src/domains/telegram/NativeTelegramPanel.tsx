import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { ApiError } from '../../api'
import { Alert, Panel } from '../../components/PageShell'
import { Button } from '../../design-system/actions/Button'
import { LoadingState } from '../../design-system/feedback/AsyncState'
import { ConfirmDialog } from '../../design-system/overlays/ConfirmDialog'
import {
  nativeTelegramClient,
  type BotJob,
  type BotJobAlternate,
  type BotJobDraft,
  type BotJobKind,
  type BotJobSchedule,
  type NativeTelegramClient,
  type NativeTelegramState,
  type ParkBot,
} from './nativeTelegramApi'
import { NativeTelegramMigrationPanel } from './NativeTelegramMigrationPanel'
import './telegram.css'

const WEEKDAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'] as const
const JOB_KIND_LABELS: Record<BotJobKind, string> = {
  report: 'Отчёт', text: 'Текст', zoom: 'Zoom', campaign: 'Кампания',
}
const TELEGRAM_ID_MAX = 2 ** 52 - 1
const RUNTIME_POLL_MS = 15_000

function deviceDateTime(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  if (!Number.isFinite(date.valueOf())) return ''
  return new Date(date.valueOf() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16)
}

function scheduleSummary(job: BotJob): string {
  const days = job.weekdays.length === WEEKDAYS.length
    ? 'Пн–Вс'
    : job.weekdays.map(day => WEEKDAYS[day]).filter(Boolean).join(', ')
  const hour = (value: number | null) => value == null ? '—' : `${String(value).padStart(2, '0')}:00`
  const schedule = job.schedule === 'daily'
    ? `В заданное время: ${job.time ?? '—'}`
    : job.schedule === 'once' ? `Один раз: ${job.run_at ? new Date(job.run_at).toLocaleString('ru-RU') : '—'} (время вашего устройства)`
      : `Каждый час: ${hour(job.start_hour)}–${hour(job.end_hour)}`
  const alternation = job.alternate === 'all' ? '' : ` · Группа ${job.alternate === 'even' ? 'A' : 'B'} через день`
  return `${JOB_KIND_LABELS[job.kind]} · ${schedule}${job.schedule === 'once' ? '' : ` · ${days || 'дни не выбраны'} · ${job.timezone || 'время парка'}`}${alternation} · ${job.enabled ? 'включено' : 'выключено'}`
}

const emptyDraft = (parkId: number): BotJobDraft => ({
  park_id: parkId, kind: 'report', title: '', enabled: false, schedule: 'daily', time: '09:00',
  weekdays: [0, 1, 2, 3, 4, 5, 6], start_hour: null, end_hour: null, text: null, url: null,
  tracker_tag: null, alternate: 'all', anchor_date: null, timezone: null, run_at: null,
})

function errorMessage(error: unknown): { text: string; conflict: boolean } {
  if (error instanceof ApiError && (error.status === 409 || error.status === 412 || error.detail === 'stale_revision')) {
    return { text: 'Настройки изменились в другой вкладке или в Telegram. Загрузите свежие данные.', conflict: true }
  }
  if (error instanceof ApiError && error.status === 403) return { text: 'Нет доступа к этому парку.', conflict: false }
  if (error instanceof ApiError && error.status === 422) return { text: 'Проверьте заполненные поля расписания.', conflict: false }
  return { text: 'Операция не выполнена. Проверьте соединение и повторите попытку.', conflict: false }
}

function parseTelegramId(value: string, positive = false): number | null | undefined {
  const trimmed = value.trim()
  if (!trimmed) return null
  if (!/^-?\d+$/.test(trimmed)) return undefined
  const parsed = Number(trimmed)
  return Number.isSafeInteger(parsed) && Math.abs(parsed) <= TELEGRAM_ID_MAX && (!positive || parsed > 0) ? parsed : undefined
}

function validateDraft(draft: BotJobDraft): string {
  if (!draft.title.trim()) return 'Укажите название задания.'
  if (draft.title.trim().length > 128) return 'Название должно быть не длиннее 128 символов.'
  if (draft.schedule !== 'once' && draft.weekdays.length === 0) return 'Выберите хотя бы один день недели.'
  if (draft.schedule === 'once' && (!draft.run_at || !Number.isFinite(Date.parse(draft.run_at)))) return 'Укажите дату и время отправки.'
  if (draft.schedule === 'once' && draft.enabled && Date.parse(draft.run_at!) <= Date.now()) return 'Для разовой отправки выберите будущее время.'
  if (draft.schedule === 'daily' && !draft.time) return 'Укажите время отправки.'
  if (draft.schedule === 'hourly' && (draft.start_hour == null || draft.end_hour == null || draft.start_hour > draft.end_hour)) return 'Укажите корректное окно отправки от 0 до 23 часов.'
  if (draft.enabled && draft.kind === 'text' && !draft.text?.trim()) return 'Введите текст сообщения.'
  if (draft.enabled && draft.kind === 'zoom' && !draft.url?.trim()) return 'Укажите ссылку Zoom.'
  if (draft.enabled && draft.kind === 'campaign' && !draft.tracker_tag?.trim()) return 'Укажите Tracker-тег кампании.'
  if (draft.enabled) {
    const source = draft.text?.trim() || draft.title.trim()
    const url = draft.url?.trim() || ''
    const mentionsLink = source.includes('{link}')
    const body = source.replaceAll('{link}', url) + (url && !mentionsLink ? `\n${url}` : '')
    const limit = draft.kind === 'report' || draft.kind === 'campaign' ? 500 : 4_096
    if (body.length > limit) return `Сообщение после подстановки ссылки длиннее ${limit} символов. Сократите текст перед включением задания.`
  }
  if (draft.schedule !== 'once' && draft.alternate !== 'all' && !draft.anchor_date) return 'Для чередования укажите опорную дату.'
  return ''
}

function normalizedDraft(draft: BotJobDraft): BotJobDraft {
  const text = (value: string | null) => value?.trim() || null
  return {
    park_id: draft.park_id,
    kind: draft.kind,
    title: draft.title.trim(),
    enabled: draft.enabled,
    schedule: draft.schedule,
    time: draft.schedule === 'daily' ? text(draft.time) : null,
    weekdays: draft.schedule === 'once' ? [] : [...draft.weekdays].sort((a, b) => a - b),
    start_hour: draft.schedule === 'hourly' ? draft.start_hour : null,
    end_hour: draft.schedule === 'hourly' ? draft.end_hour : null,
    text: text(draft.text), url: text(draft.url), tracker_tag: text(draft.tracker_tag),
    alternate: draft.schedule === 'once' ? 'all' : draft.alternate,
    anchor_date: draft.schedule === 'once' ? null : text(draft.anchor_date),
    timezone: draft.timezone?.trim() || null,
    run_at: draft.schedule === 'once' ? draft.run_at : null,
  }
}

export function NativeTelegramPanel({ client = nativeTelegramClient, initialParkId = null, showMigration = false }: { client?: NativeTelegramClient; initialParkId?: number | null; showMigration?: boolean }) {
  const [data, setData] = useState<NativeTelegramState | null>(null)
  const [selectedParkId, setSelectedParkId] = useState<number | null>(null)
  const selectedParkRef = useRef<number | null>(null)
  const loadGeneration = useRef(0)
  const contextGeneration = useRef(0)
  const runtimeGeneration = useRef(0)
  const runtimePending = useRef<Promise<void> | null>(null)
  const [chatId, setChatId] = useState('')
  const [threadId, setThreadId] = useState('')
  const [draft, setDraft] = useState<BotJobDraft | null>(null)
  const [editing, setEditing] = useState<BotJob | null>(null)
  const [deleting, setDeleting] = useState<BotJob | null>(null)
  const [sending, setSending] = useState<{ job: BotJob; requestId: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [conflict, setConflict] = useState(false)
  const [allParks, setAllParks] = useState(false)
  const [kindFilter, setKindFilter] = useState<BotJobKind | ''>('')
  const [search, setSearch] = useState('')
  const [configurationChanged, setConfigurationChanged] = useState(false)
  const configurationLocked = useRef(false)
  const configurationSnapshot = useRef(data)
  useLayoutEffect(() => { configurationSnapshot.current = data }, [data])

  const load = useCallback(async (preferredParkId: number | null, preferPreferred = false) => {
    const generation = ++loadGeneration.current
    setLoading(true); setError(''); setConflict(false)
    try {
      const value = await client.getAdmin()
      if (loadGeneration.current !== generation) return
      const current = selectedParkRef.current
      const preferredAvailable = value.parks.some(park => park.id === preferredParkId)
      const target = preferPreferred && preferredAvailable
        ? preferredParkId
        : value.parks.some(park => park.id === current)
          ? current
          : preferredAvailable ? preferredParkId : value.parks[0]?.id ?? null
      const selected = value.parks.find(park => park.id === target) ?? null
      contextGeneration.current += 1
      setData(value)
      setConfigurationChanged(false)
      selectedParkRef.current = target
      setSelectedParkId(target)
      setChatId(selected?.chat_id == null ? '' : String(selected.chat_id))
      setThreadId(selected?.thread_id == null ? '' : String(selected.thread_id))
      setDraft(null); setEditing(null); setDeleting(null); setNotice('')
      setSending(null)
    } catch (caught) {
      if (loadGeneration.current !== generation) return
      const mapped = errorMessage(caught); setError(mapped.text); setConflict(mapped.conflict)
    } finally {
      if (loadGeneration.current === generation) setLoading(false)
    }
  }, [client])

  useEffect(() => {
    void load(initialParkId, true)
    return () => {
      loadGeneration.current += 1
      contextGeneration.current += 1
    }
  }, [initialParkId, load])

  const refreshRuntime = useCallback(() => {
    if (runtimePending.current) return runtimePending.current
    const generation = ++runtimeGeneration.current
    const context = contextGeneration.current
    const startedLocked = configurationLocked.current
    const work = client.getAdmin().then(value => {
      if (runtimeGeneration.current !== generation || contextGeneration.current !== context) return
      if (startedLocked || configurationLocked.current) {
        const previous = configurationSnapshot.current
        if (previous && JSON.stringify([previous.parks, previous.jobs]) !== JSON.stringify([value.parks, value.jobs])) setConfigurationChanged(true)
        setData(current => current ? { ...current, health: value.health, deliveries: value.deliveries } : current)
      } else {
        setData(value)
        const selected = value.parks.find(item => item.id === selectedParkRef.current) ?? value.parks[0]
        if ((selected?.id ?? null) !== selectedParkRef.current) {
          contextGeneration.current += 1
          selectedParkRef.current = selected?.id ?? null
          setSelectedParkId(selected?.id ?? null)
        }
        setChatId(selected?.chat_id == null ? '' : String(selected.chat_id))
        setThreadId(selected?.thread_id == null ? '' : String(selected.thread_id))
        setConfigurationChanged(false)
      }
    }).catch(() => {
      // Keep the last bounded runtime snapshot; the next visible poll retries.
    }).finally(() => {
      if (runtimePending.current === work) runtimePending.current = null
    })
    runtimePending.current = work
    return work
  }, [client])

  const refreshConfiguration = useCallback(async () => {
    runtimeGeneration.current += 1
    const generation = contextGeneration.current
    const value = await client.getAdmin()
    if (contextGeneration.current === generation) {
      runtimeGeneration.current += 1
      setData(value)
    }
  }, [client])

  useEffect(() => {
    let active = true
    let timer: number | undefined
    const clear = () => { window.clearTimeout(timer); timer = undefined }
    const schedule = () => {
      clear()
      if (active && document.visibilityState === 'visible') {
        timer = window.setTimeout(async () => { await refreshRuntime(); schedule() }, RUNTIME_POLL_MS)
      }
    }
    const visibilityChanged = () => {
      clear()
      if (active && document.visibilityState === 'visible') void refreshRuntime().finally(schedule)
    }
    document.addEventListener('visibilitychange', visibilityChanged)
    schedule()
    return () => {
      active = false
      clear()
      runtimeGeneration.current += 1
      runtimePending.current = null
      document.removeEventListener('visibilitychange', visibilityChanged)
    }
  }, [refreshRuntime])
  const park = data?.parks.find(item => item.id === selectedParkId) ?? null
  const chatDirty = chatId !== (park?.chat_id == null ? '' : String(park.chat_id))
    || threadId !== (park?.thread_id == null ? '' : String(park.thread_id))
  const isConfigurationLocked = busy || Boolean(draft || deleting || sending) || chatDirty
  useLayoutEffect(() => { configurationLocked.current = isConfigurationLocked }, [isConfigurationLocked])

  const selectPark = (id: number) => {
    const selected = data?.parks.find(item => item.id === id) ?? null
    loadGeneration.current += 1
    contextGeneration.current += 1
    selectedParkRef.current = id
    setSelectedParkId(id)
    setChatId(selected?.chat_id == null ? '' : String(selected.chat_id))
    setThreadId(selected?.thread_id == null ? '' : String(selected.thread_id))
    setDraft(null); setEditing(null); setDeleting(null); setError(''); setNotice(''); setConflict(false); setLoading(false); setBusy(false)
    setSending(null)
  }

  const jobs = useMemo(() => data?.jobs.filter(job => {
    if (!allParks && job.park_id !== selectedParkId) return false
    if (kindFilter && job.kind !== kindFilter) return false
    const parkName = data.parks.find(item => item.id === job.park_id)?.name ?? ''
    return `${job.title} ${parkName} ${job.text ?? ''}`.toLocaleLowerCase('ru-RU').includes(search.trim().toLocaleLowerCase('ru-RU'))
  }) ?? [], [data, selectedParkId, allParks, kindFilter, search])
  const deliveries = useMemo(() => data?.deliveries.filter(item => item.park_id === selectedParkId) ?? [], [data?.deliveries, selectedParkId])
  const serviceOffline = data?.health.state === 'offline'

  const fail = (caught: unknown) => {
    const mapped = errorMessage(caught); setError(mapped.text); setConflict(mapped.conflict)
  }
  const updateParkInData = (updated: ParkBot) => setData(current => current ? ({
    ...current, parks: current.parks.map(item => item.id === updated.id ? updated : item),
  }) : current)
  const updateJobInData = (updated: BotJob) => setData(current => current ? ({
    ...current, jobs: current.jobs.some(item => item.id === updated.id)
      ? current.jobs.map(item => item.id === updated.id ? updated : item)
      : [...current.jobs, updated],
  }) : current)

  const savePark = async () => {
    if (!park) return
    const parsedChat = parseTelegramId(chatId)
    const parsedThread = parseTelegramId(threadId, true)
    if (parsedChat === undefined || parsedThread === undefined) {
      setError('ID чата должен быть безопасным целым числом не длиннее 52 бит, а ID темы — ещё и положительным.'); return
    }
    const generation = contextGeneration.current
    runtimeGeneration.current += 1
    setBusy(true); setError(''); setNotice(''); setConflict(false)
    try {
      const updated = await client.updatePark(park.id, { chat_id: parsedChat, thread_id: parsedThread, revision: park.revision })
      if (contextGeneration.current !== generation) return
      updateParkInData(updated)
      setNotice(parsedChat == null ? 'Доставка в парк отключена.' : 'Чат и тема сохранены.')
    } catch (caught) { if (contextGeneration.current === generation) fail(caught) }
    finally { if (contextGeneration.current === generation) setBusy(false) }
  }

  const openCreate = () => { if (park) { setEditing(null); setDraft(emptyDraft(park.id)) } }
  const openEdit = (job: BotJob) => { setEditing(job); setDraft({ ...job, weekdays: [...job.weekdays] }) }
  const saveJob = async (event: FormEvent) => {
    event.preventDefault()
    if (!draft) return
    const validation = validateDraft(draft)
    if (validation) { setError(validation); return }
    const body = normalizedDraft(draft)
    const generation = contextGeneration.current
    runtimeGeneration.current += 1
    setBusy(true); setError(''); setNotice(''); setConflict(false)
    try {
      const updated = editing
        ? await client.updateJob(editing.id, { ...body, revision: editing.revision })
        : await client.createJob(body)
      if (contextGeneration.current !== generation) return
      updateJobInData(updated); setDraft(null); setEditing(null)
      setNotice(editing ? 'Задание обновлено.' : 'Задание создано.')
    } catch (caught) { if (contextGeneration.current === generation) fail(caught) }
    finally { if (contextGeneration.current === generation) setBusy(false) }
  }

  const toggleJob = async (job: BotJob) => {
    const generation = contextGeneration.current
    runtimeGeneration.current += 1
    setBusy(true); setError(''); setNotice(''); setConflict(false)
    try {
      const updated = await client.updateJob(job.id, { ...normalizedDraft(job), enabled: !job.enabled, revision: job.revision })
      if (contextGeneration.current !== generation) return
      updateJobInData(updated)
    } catch (caught) { if (contextGeneration.current === generation) fail(caught) }
    finally { if (contextGeneration.current === generation) setBusy(false) }
  }

  const deleteJob = async () => {
    if (!deleting) return
    const generation = contextGeneration.current
    runtimeGeneration.current += 1
    const target = deleting
    setBusy(true); setError(''); setConflict(false)
    try {
      await client.deleteJob(target.id, target.revision)
      if (contextGeneration.current !== generation) return
      setData(current => current ? ({ ...current, jobs: current.jobs.filter(item => item.id !== target.id) }) : current)
      setDeleting(null); setNotice('Задание удалено.')
    } catch (caught) { if (contextGeneration.current === generation) { setDeleting(null); fail(caught) } }
    finally { if (contextGeneration.current === generation) setBusy(false) }
  }

  const sendNow = async () => {
    if (!sending) return
    const generation = contextGeneration.current
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await client.runJob(sending.job.id, { revision: sending.job.revision, request_id: sending.requestId, allow_disabled: !sending.job.enabled })
      if (contextGeneration.current !== generation) return
      setSending(null)
      setNotice(result.created ? 'Отправка поставлена в очередь. Результат появится в журнале.' : 'Эта отправка уже в очереди или обработана. Проверьте журнал.')
      void refreshRuntime()
    } catch (caught) { if (contextGeneration.current === generation) fail(caught) }
    finally { if (contextGeneration.current === generation) setBusy(false) }
  }

  if (loading && !data) return <LoadingState label="Загружаем настройки Telegram" variant="page" />
  return <div className="rp-native-telegram">
    {error ? <Alert tone="error">{error}</Alert> : null}
    {conflict ? <Button onClick={() => void load(selectedParkRef.current)} variant="secondary">Загрузить свежие данные</Button> : null}
    {notice ? <Alert tone="success">{notice}</Alert> : null}
    {!data ? <Button onClick={() => void load(initialParkId, true)} variant="secondary">Повторить</Button> : <>
      {showMigration ? <NativeTelegramMigrationPanel client={client} onApplied={refreshConfiguration} /> : null}
      <Panel density="dense" hint="Состояние процесса не подтверждает доставку сообщений — результаты отправок находятся в журнале." title="Состояние сервиса">
        {serviceOffline ? <Alert tone="error">Сервис бота офлайн. Последние отдельные проверки могли устареть.</Alert> : null}
        <div className="rp-telegram-health">
          <div><span>Связь с Telegram</span><strong>{serviceOffline ? 'Сервис офлайн' : data.health.telegram_ok === true ? 'Telegram отвечает' : data.health.telegram_ok === false ? 'Telegram не отвечает' : 'Не проверено'}</strong></div>
          <div><span>Обработка расписания</span><strong>{serviceOffline ? 'Сервис офлайн' : data.health.scheduler_ok === true ? 'Планировщик работает' : data.health.scheduler_ok === false ? 'Планировщик не отвечает' : 'Не проверено'}</strong></div>
        </div>
        {data.health.updated_at ? <p className="rp-telegram-muted">Обновлено {new Date(data.health.updated_at).toLocaleString('ru-RU')}.</p> : null}
        {data.health.last_error ? <p className="rp-telegram-muted">Последняя ошибка: <code>{data.health.last_error}</code></p> : null}
      </Panel>
      {data.parks.every(item => item.chat_id == null) && data.jobs.length === 0
        ? <Alert tone="info">Настройка Telegram не завершена: укажите чат парка и создайте хотя бы одно задание.</Alert>
        : null}
      <label className="field"><span className="field-label">Парк Telegram</span>
        <select aria-label="Парк Telegram" onChange={event => selectPark(Number(event.target.value))} value={selectedParkId ?? ''}>
          {data.parks.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>
      {configurationChanged ? <Alert tone="info">Задания или настройки изменились в Telegram либо другой вкладке. Ваш ввод сохранён. Завершите редактирование и обновите список.</Alert> : null}
      {!park ? <p>Нет доступных парков.</p> : <>
        <Panel density="dense" hint={`Часовой пояс: ${park.timezone}. Пустой ID чата отключает доставку.`} title="Чат парка">
          <div className="rp-telegram-form-row">
            <label className="field"><span className="field-label">ID чата</span><input inputMode="numeric" onChange={event => setChatId(event.target.value)} value={chatId} /></label>
            <label className="field"><span className="field-label">ID темы</span><input inputMode="numeric" onChange={event => setThreadId(event.target.value)} value={threadId} /></label>
          </div>
          <Button busy={busy} onClick={() => void savePark()}>Сохранить чат</Button>
        </Panel>

        <Panel actions={<div className="rp-telegram-actions"><Button disabled={isConfigurationLocked} onClick={() => void refreshRuntime()} size="compact" variant="secondary">Обновить задания</Button><Button disabled={busy} onClick={openCreate} size="compact">Новое задание</Button></div>} density="dense" title="Задания">
          <p className="rp-telegram-muted">Всего доступно: {data.jobs.length}. Показано: {jobs.length}. Новые задания создаются для парка «{park.name}».</p>
          <label className="toggle"><input checked={allParks} onChange={event => setAllParks(event.target.checked)} type="checkbox" />Все доступные парки</label>
          <div className="rp-telegram-form-row">
            <label className="field"><span className="field-label">Вид рассылки</span><select value={kindFilter} onChange={event => setKindFilter(event.target.value as BotJobKind | '')}><option value="">Все виды</option><option value="zoom">Zoom</option><option value="text">Уведомления и объявления</option><option value="report">PNG и отчёты</option><option value="campaign">Кампании</option></select></label>
            <label className="field"><span className="field-label">Поиск рассылки</span><input value={search} onChange={event => setSearch(event.target.value)} placeholder="Название, парк или текст" /></label>
          </div>
          {jobs.length === 0 ? <p className="rp-telegram-muted">По выбранным условиям заданий нет.</p> : <div className="rp-telegram-jobs">
            {jobs.map(job => <article className="rp-telegram-job" key={job.id}>
              <div><strong>{job.title}</strong>{allParks ? <small>{data.parks.find(item => item.id === job.park_id)?.name}</small> : null}<small>{scheduleSummary(job)}</small></div>
              <div className="rp-telegram-actions">
                <Button disabled={busy} onClick={() => openEdit(job)} size="compact" variant="secondary">Изменить</Button>
                <Button aria-label={`${job.enabled ? 'Отключить' : 'Включить'} ${job.title}`} disabled={busy} onClick={() => void toggleJob(job)} size="compact" variant="secondary">{job.enabled ? 'Отключить' : 'Включить'}</Button>
                <Button aria-label={`Отправить сейчас ${job.title}`} disabled={busy} onClick={() => setSending({ job, requestId: crypto.randomUUID() })} size="compact" variant="secondary">Отправить сейчас</Button>
                <Button aria-label={`Удалить ${job.title}`} disabled={busy} onClick={() => setDeleting(job)} size="compact" variant="danger">Удалить</Button>
              </div>
            </article>)}
          </div>}
          {draft ? <JobForm busy={busy} draft={draft} editing={Boolean(editing)} onCancel={() => { setDraft(null); setEditing(null) }} onChange={setDraft} onSubmit={saveJob} /> : null}
        </Panel>

        <Panel density="dense" title="Журнал доставки">
          {deliveries.length === 0 ? <p className="rp-telegram-muted">Доставок для парка пока нет.</p> : <div className="rp-telegram-deliveries">
            {deliveries.map(item => <article key={item.id}>
              <div><strong>{item.title}</strong><small>{new Date(item.scheduled_at).toLocaleString('ru-RU')}</small></div>
              <div><span className={`badge ${item.state === 'sent' ? 'badge-ok' : 'badge-warn'}`}>{item.state === 'sent' ? 'Доставлено' : item.state === 'failed' ? 'Ошибка' : item.state === 'unknown' ? 'Неизвестно' : item.state}</span>{item.error_code ? <><span>{item.error_code === 'telegram_unavailable' ? 'Telegram недоступен' : 'Код ошибки'}</span><code>{item.error_code}</code></> : null}</div>
            </article>)}
          </div>}
        </Panel>
      </>}
    </>}
    <ConfirmDialog confirmLabel="Удалить" description={deleting ? `Задание «${deleting.title}» будет удалено без восстановления.` : ''} onConfirm={() => void deleteJob()} onOpenChange={open => { if (!open) setDeleting(null) }} open={Boolean(deleting)} pending={busy} title="Удалить задание?" />
    <ConfirmDialog confirmLabel="Отправить" description={sending ? `«${sending.job.title}» будет отправлено в чат парка «${data?.parks.find(item => item.id === sending.job.park_id)?.name ?? ''}» сейчас, независимо от расписания. ${!sending.job.enabled ? 'Регулярное задание останется выключенным.' : ''}` : ''} onConfirm={() => void sendNow()} onOpenChange={open => { if (!open && !busy) setSending(null) }} open={Boolean(sending)} pending={busy} title="Отправить сообщение сейчас?" />
  </div>
}

function JobForm({ draft, editing, busy, onChange, onCancel, onSubmit }: {
  draft: BotJobDraft; editing: boolean; busy: boolean
  onChange: (draft: BotJobDraft) => void; onCancel: () => void; onSubmit: (event: FormEvent) => void
}) {
  const patch = <K extends keyof BotJobDraft>(key: K, value: BotJobDraft[K]) => onChange({ ...draft, [key]: value })
  return <form className="rp-telegram-job-form" onSubmit={onSubmit}>
    <h3>{editing ? 'Изменить задание' : 'Новое задание'}</h3>
    <div className="rp-telegram-form-row">
      <label className="field"><span className="field-label">Тип задания</span><select onChange={event => onChange({ ...draft, kind: event.target.value as BotJobKind, timezone: event.target.value === 'zoom' ? draft.timezone || 'Europe/Moscow' : draft.timezone })} value={draft.kind}>
        <option value="report">Отчёт</option><option value="text">Текст</option><option value="zoom">Zoom</option><option value="campaign">Кампания</option>
      </select></label>
      <label className="field"><span className="field-label">Название</span><input maxLength={128} onChange={event => patch('title', event.target.value)} value={draft.title} /></label>
    </div>
    <label className="toggle"><input checked={draft.enabled} onChange={event => patch('enabled', event.target.checked)} type="checkbox" />Задание включено</label>
    <div className="rp-telegram-form-row">
      <label className="field"><span className="field-label">Расписание</span><select onChange={event => patch('schedule', event.target.value as BotJobSchedule)} value={draft.schedule}><option value="daily">В заданное время</option><option value="hourly">Каждый час в окне</option><option value="once">Один раз</option></select></label>
      {draft.schedule === 'daily'
        ? <label className="field"><span className="field-label">Время</span><input onChange={event => patch('time', event.target.value)} type="time" value={draft.time ?? ''} /></label>
        : draft.schedule === 'once'
          ? <label className="field"><span className="field-label">Дата и время отправки</span><input onChange={event => patch('run_at', event.target.value ? new Date(event.target.value).toISOString() : null)} type="datetime-local" value={deviceDateTime(draft.run_at)} /><span className="field-hint">Время вашего устройства: {Intl.DateTimeFormat().resolvedOptions().timeZone}. Отправка выполнится один раз.</span></label>
        : <div className="rp-telegram-form-row"><label className="field"><span className="field-label">Начальный час</span><input max="23" min="0" onChange={event => patch('start_hour', event.target.value === '' ? null : Number(event.target.value))} type="number" value={draft.start_hour ?? ''} /></label><label className="field"><span className="field-label">Конечный час</span><input max="23" min="0" onChange={event => patch('end_hour', event.target.value === '' ? null : Number(event.target.value))} type="number" value={draft.end_hour ?? ''} /></label></div>}
    </div>
    {draft.schedule !== 'once' ? <>
      <label className="field"><span className="field-label">Часовой пояс расписания</span><input list="telegram-timezones" onChange={event => patch('timezone', event.target.value || null)} placeholder="Часовой пояс парка" value={draft.timezone ?? ''} /><datalist id="telegram-timezones"><option value="Europe/Moscow" /><option value="Asia/Almaty" /><option value="Europe/Kaliningrad" /><option value="Asia/Yekaterinburg" /></datalist><span className="field-hint">Для общих созвонов по московскому времени выберите Europe/Moscow. Пустое поле — время парка.</span></label>
      <fieldset className="rp-telegram-weekdays"><legend>Дни недели</legend>{WEEKDAYS.map((label, day) => <label key={label}><input checked={draft.weekdays.includes(day)} onChange={event => patch('weekdays', event.target.checked ? [...draft.weekdays, day] : draft.weekdays.filter(value => value !== day))} type="checkbox" />{label}</label>)}</fieldset>
    </> : null}
    <label className="field"><span className="field-label">Текст</span><textarea aria-label="Текст" maxLength={20_000} onChange={event => patch('text', event.target.value)} value={draft.text ?? ''} /><span className="field-hint">Можно использовать {'{link}'}. При включении лимит: {draft.kind === 'report' || draft.kind === 'campaign' ? '500 символов для подписи к изображению' : '4096 символов'} после подстановки ссылки.</span></label>
    {(draft.kind === 'report' || draft.kind === 'zoom' || draft.kind === 'campaign') ? <label className="field"><span className="field-label">Ссылка</span><input maxLength={2_048} onChange={event => patch('url', event.target.value)} type="url" value={draft.url ?? ''} /></label> : null}
    {(draft.kind === 'report' || draft.kind === 'campaign') ? <label className="field"><span className="field-label">Tracker-тег</span><input maxLength={128} onChange={event => patch('tracker_tag', event.target.value)} value={draft.tracker_tag ?? ''} /></label> : null}
    {draft.schedule !== 'once' ? <div className="rp-telegram-form-row">
      <label className="field"><span className="field-label">Чередование</span><select onChange={event => patch('alternate', event.target.value as BotJobAlternate)} value={draft.alternate}><option value="all">Каждый выбранный день</option><option value="even">Группа A — через день с опорной даты</option><option value="odd">Группа B — через день после опорной даты</option></select></label>
      <label className="field"><span className="field-label">Опорная дата</span><input onChange={event => patch('anchor_date', event.target.value)} type="date" value={draft.anchor_date ?? ''} /><span className="field-hint">Общий день A для всех чередующихся парков. Следующий день — B; выходные не сбрасывают цикл.</span></label>
    </div> : null}
    <div className="rp-telegram-actions"><Button busy={busy} type="submit">{editing ? 'Сохранить задание' : 'Создать задание'}</Button><Button disabled={busy} onClick={onCancel} type="button" variant="secondary">Отмена</Button></div>
  </form>
}
