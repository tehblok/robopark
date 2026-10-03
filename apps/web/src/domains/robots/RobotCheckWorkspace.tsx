import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { api, type DiagnosticEvent, type DiagnosticView, type EmergencySection, type EmergencySectionDetail, type EmergencySnapshot } from '../../api'
import { canAccessRoute, type AccessUser } from '../../app/routing/accessPolicy'
import { InspectionMap } from '../../components/emergency/InspectionMap'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import type { DomainError } from '../../shared/api/classifyApiError'
import { useOnlineStatus } from '../../shared/browser/useOnlineStatus'
import { RobotCheckSummary } from './RobotCheckSummary'
import { formatRobotMode, ROBOT_OBSERVATION_FUTURE_TOLERANCE_MS } from './robotDetailModel'
import { RobotCheckNavigation } from './RobotCheckNavigation'
import { RobotDiagnosticDiagram } from './RobotDiagnosticDiagram'
import { DiagnosticEventDetails } from './DiagnosticEventDetails'
import { chooseAutomaticDiagnosticSelection, chooseAutomaticView, isLocalizedEvent, leadingDiagnosticEvent } from './diagnosticPresentation'
import { checkAccessIdentity, checkTabs, classifyCheckError } from './robotCheckUrl'
import { ROBOT_POLL_MS } from './polling'
import { useVisibilityPolling } from './useVisibilityPolling'
import './robot-check.css'

export type RobotCheckApiClient = Pick<typeof api, 'emergencySnapshot' | 'emergencySection'>
export type RobotCheckWorkspaceProps = {
  vin: string; user: AccessUser & { id?: number }; sections: EmergencySection[]; activeTab: string
  onTabChange: (tab: string) => void; apiClient?: RobotCheckApiClient
  onAuthorizationFailure?: (failure: DomainError) => void
  onSnapshot?: (snapshot: EmergencySnapshot | null) => void
  renderSummary?: (snapshot: EmergencySnapshot, failure: DomainError | null, refresh: () => void) => ReactNode
  renderTasks?: (snapshot: EmergencySnapshot | null, failure: DomainError | null, refresh: () => void) => ReactNode
}
export function CheckError({ failure, user, onRetry }: { failure: DomainError; user: AccessUser; onRetry?: () => void }) {
  return <><ErrorState {...failure} onRetry={failure.retryable ? onRetry : undefined} />
    {failure.kind === 'configuration' && canAccessRoute(user, 'admin-robot-check') ? <Link to="/admin/emergency/config">Открыть настройки</Link> : null}</>
}
export function RobotCheckController({ vin, user, sections, activeTab, onTabChange, apiClient = api, onAuthorizationFailure, onSnapshot, renderSummary, renderTasks }: RobotCheckWorkspaceProps) {
  const online = useOnlineStatus()
  const [snapshot, setSnapshot] = useState<EmergencySnapshot | null>(null)
  const [now, setNow] = useState(() => new Date())
  useEffect(() => { onSnapshot?.(snapshot) }, [onSnapshot, snapshot])
  useEffect(() => {
    if (!snapshot) return
    const tick = () => setNow(new Date())
    const interval = window.setInterval(() => { if (!document.hidden) tick() }, 30_000)
    const observedAt = Date.parse(snapshot.observed_at)
    const expiresIn = observedAt + 300_001 - Date.now()
    const expiry = Number.isFinite(expiresIn) && expiresIn > 0 ? window.setTimeout(tick, expiresIn) : undefined
    const onVisibility = () => { if (!document.hidden) tick() }
    document.addEventListener('visibilitychange', onVisibility)
    return () => { window.clearInterval(interval); window.clearTimeout(expiry); document.removeEventListener('visibilitychange', onVisibility) }
  }, [snapshot])
  const [snapshotError, setSnapshotError] = useState<DomainError | null>(null)
  const [details, setDetails] = useState<Record<string, EmergencySectionDetail>>({})
  const [errors, setErrors] = useState<Record<string, DomainError | null>>({})
  const [denied, setDenied] = useState<DomainError | null>(null)
  const [follow, setFollow] = useState(true)
  const [schemeHost, setSchemeHost] = useState<HTMLDivElement | null>(null)
  const detailRef = useRef<HTMLDivElement>(null)
  const [diagnosticSelection, setDiagnosticSelection] = useState<{ view: DiagnosticView; eventId: string | null; blockId: string | null }>({ view: 'top', eventId: null, blockId: sections[0]?.id ?? null })
  // Owned by the same VIN/access/park lifetime as the snapshot, never by a tab.
  const manualBlock = useRef(false)
  const manualEventId = useRef<string | null>(null)
  // This cache belongs to one VIN/access lifetime and never persists to disk.
  const cache = useRef(new Map<string, { data?: unknown; updatedAt: number; pending?: Promise<unknown> }>())
  const cachedRequest = useCallback(<T,>(key: string, load: () => Promise<T>, force: boolean): Promise<T> => {
    const stored = cache.current.get(key)
    if (stored?.pending) return stored.pending as Promise<T>
    if (!force && stored?.data !== undefined && Date.now() - stored.updatedAt < ROBOT_POLL_MS) return Promise.resolve(stored.data as T)
    const entry = stored ?? { updatedAt: 0 }
    cache.current.set(key, entry)
    const pending = Promise.resolve().then(load).then(data => {
      entry.data = data; entry.updatedAt = Date.now(); return data
    }).finally(() => { if (entry.pending === pending) entry.pending = undefined })
    entry.pending = pending
    return pending
  }, [])
  const denial = useRef(false)
  const generation = useRef(0)
  const notify = useRef(onAuthorizationFailure)
  useLayoutEffect(() => { notify.current = onAuthorizationFailure }, [onAuthorizationFailure])
  const tabs = checkTabs(sections)
  const tab = tabs.find(item => item.id === activeTab) ?? tabs[0]
  useLayoutEffect(() => {
    generation.current += 1
    return () => { generation.current += 1 }
  }, [vin, tab.id, tab.kind, apiClient])
  const task = useCallback(async (force = false) => {
    const requestedGeneration = generation.current
    const current = () => requestedGeneration === generation.current && !denial.current
    const observeFailure = (error: unknown, section?: string) => {
      if (!current()) return
      const failure = classifyCheckError(error, user)
      if (failure.kind === 'unauthorized' || failure.kind === 'forbidden') {
        denial.current = true
        cache.current.clear(); setSnapshot(null); setDetails({}); setErrors({}); setSnapshotError(null); setDenied(failure)
        notify.current?.(failure)
      } else if (section) setErrors(previous => ({ ...previous, [section]: failure }))
      else {
        if (!['offline', 'timeout', 'server'].includes(failure.kind)) setSnapshot(null)
        setSnapshotError(failure)
      }
    }
    if (!current()) return
    // Observe both independently: denial cannot wait for a hung sibling.
    const snapshotRequest = cachedRequest('snapshot', () => apiClient.emergencySnapshot(vin), force).then(value => {
      if (current()) {
        setSnapshot(value); setNow(new Date()); setSnapshotError(null)
        const events = value.diagnostic_events ?? []
        const automatic = chooseAutomaticDiagnosticSelection(events, value.readings ?? [], sections.map(section => section.id))
        const manualEvent = events.find(event => event.id === manualEventId.current)
        if (!manualEvent) manualEventId.current = null
        setDiagnosticSelection(current => ({
          view: manualEvent?.view ?? automatic.view,
          blockId: manualBlock.current && sections.some(section => section.id === current.blockId) ? current.blockId : automatic.blockId,
          eventId: manualEvent?.id ?? automatic.eventId,
        }))
      }
    }, error => { observeFailure(error); throw error })
    const requests = [snapshotRequest]
    if (tab.kind === 'section') requests.push(cachedRequest(`section:${tab.id}`, () => apiClient.emergencySection(vin, tab.id), force).then(value => {
      if (current()) { setDetails(previous => ({ ...previous, [tab.id]: value })); setErrors(previous => ({ ...previous, [tab.id]: null })) }
    }, error => { observeFailure(error, tab.id); throw error }))
    const results = await Promise.allSettled(requests)
    const rejected = results.find(result => result.status === 'rejected')
    if (rejected?.status === 'rejected') throw rejected.reason
  }, [vin, tab.id, tab.kind, apiClient, cachedRequest, sections, user])
  const { pending, retryAfterAt, refreshNow } = useVisibilityPolling({ enabled: !denied, online, task, scopeKey: vin })
  const retryDeferred = retryAfterAt !== null
  const refresh = () => { void refreshNow() }
  if (denied) return <CheckError failure={denied} user={user} />
  const events = snapshot?.diagnostic_events ?? []
  const observedAt = snapshot ? Date.parse(snapshot.observed_at) : NaN
  const diagnosticsFresh = Boolean(snapshot && online && !snapshotError && !snapshot.stale && snapshot.online === true
    && Number.isFinite(observedAt) && observedAt <= now.getTime() + ROBOT_OBSERVATION_FUTURE_TOLERANCE_MS
    && now.getTime() - observedAt <= 300_000)
  const showEvent = (event: DiagnosticEvent) => {
    if (!isLocalizedEvent(event)) return
    manualEventId.current = event.id
    setDiagnosticSelection(current => ({ ...current, view: event.view, eventId: event.id }))
  }
  const showLeadingError = () => {
    manualEventId.current = null
    setDiagnosticSelection(current => ({ ...current, view: chooseAutomaticView(events), eventId: leadingDiagnosticEvent(events)?.id ?? null }))
  }
  const section = details[tab.id]
  const sectionError = errors[tab.id]
  const sectionReadings = tab.kind === 'section' ? snapshot?.readings?.filter(reading => reading.section_id === tab.id) ?? [] : []
  const hasSectionReadings = sectionReadings.length > 0
  const diagnosticBlocks = tab.kind === 'section' ? sections.filter(block => block.id === tab.id) : sections
  const identity = <div className="rp-check-first-level">
      {snapshot ? renderSummary ? renderSummary(snapshot, snapshotError, refresh) : <RobotCheckSummary snapshot={snapshot} online={online} failed={Boolean(snapshotError)} pending={pending} retryDeferred={retryDeferred} now={now} onRefresh={refresh} onShowDiagnostic={() => { showLeadingError(); onTabChange('scheme') }} />
        : <section className="rp-check-summary" aria-busy={pending}>
          {!online ? <p role="status">Нет сети на этом устройстве</p> : null}
          {!snapshotError && online ? <LoadingState label="Загружаем данные робота" /> : null}
          {!online ? <Button onClick={refresh}>Повторить проверку</Button> : null}
        </section>}
      {snapshotError && (!renderSummary || !snapshot) ? <div className="rp-check-warning"><CheckError failure={snapshotError} user={user} onRetry={retryDeferred ? undefined : refresh} />{retryDeferred ? <p role="status">Сервер ограничил частоту запросов. Проверка повторится автоматически.</p> : null}{snapshot ? <p>Показаны последние полученные данные.</p> : null}
        {snapshotError.kind === 'not-found' ? <Link to="/robots">К поиску роботов</Link> : null}</div> : null}
      {snapshot ? <Button className="rp-check-jump" variant="secondary" onClick={() => detailRef.current?.scrollIntoView({ block: 'start', behavior: globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })}>К разделам проверки</Button> : null}
      {snapshot ? <RobotDiagnosticDiagram faults={snapshot.wheels_fault} events={events} readings={snapshot.readings ?? []} blocks={diagnosticBlocks} selectedBlockId={tab.kind === 'section' ? tab.id : diagnosticSelection.blockId} view={diagnosticSelection.view} selectedEventId={diagnosticSelection.eventId} detailHost={tab.kind === 'scheme' || hasSectionReadings ? schemeHost : null}
        onSelectEvent={event => { manualEventId.current = event.id; setDiagnosticSelection(current => ({ ...current, eventId: event.id })) }}
        onBlockChange={blockId => { manualBlock.current = true; setDiagnosticSelection(current => ({ ...current, blockId })) }}
        onShowError={showLeadingError} onRevealEvent={showEvent} onOpenErrors={() => onTabChange('errors')} /> : null}
    </div>
  const detail = <div className="rp-check-detail" ref={detailRef}>
      <RobotCheckNavigation tabs={tabs} activeId={tab.id} onChange={onTabChange} />
      <section className="rp-check-panel" role="tabpanel" tabIndex={0} id={`robot-check-panel-${tab.id}`} aria-labelledby={`robot-check-tab-${tab.id}`}>
        {tab.kind === 'state' ? <dl className="rp-check-telemetry"><div><dt>Режим</dt><dd>{formatRobotMode(snapshot?.mode)}</dd></div><div><dt>Связь робота</dt><dd>{snapshot?.online == null ? 'Нет данных' : snapshot.online ? 'На связи' : 'Не в сети'}</dd></div><div><dt>Заряд</dt><dd>{snapshot?.charge_percent == null ? 'Нет данных' : `${snapshot.charge_percent} %`}</dd></div></dl> : null}
        {tab.kind === 'errors' ? snapshot ? <>
          {!diagnosticsFresh && <p role="status">Данные диагностики устарели; отсутствие ошибок не подтверждено.</p>}
          {snapshot.error_banner ? <p role="status">{snapshot.error_banner}</p> : !events.length && diagnosticsFresh ? <p>Сообщения об ошибках не получены.</p> : null}
          {events.length ? <>
            <Button variant="secondary" disabled={!leadingDiagnosticEvent(events)} onClick={() => { showLeadingError(); onTabChange('scheme') }}>Показать ошибку</Button>
            <ul className="rp-check-events" aria-label="Диагностические события">{events.map(event => <li key={event.id}>
              <DiagnosticEventDetails event={event} />
              {isLocalizedEvent(event) ? <Button variant="secondary" onClick={() => { showEvent(event); onTabChange('scheme') }}>Посмотреть на схеме</Button> : null}
            </li>)}</ul>
          </> : null}
          {snapshot.wheels_fault.length ? <><p>Есть сообщения о неисправности колёс.</p><Button variant="secondary" onClick={() => onTabChange('scheme')}>Посмотреть на схеме</Button></> : diagnosticsFresh ? <p>Данные о неисправностях колёс не сообщены.</p> : null}
        </> : <EmptyState title="Данные диагностики не получены" /> : null}
        {tab.kind === 'tasks' ? renderTasks?.(snapshot, snapshotError, refresh) ?? <EmptyState title="Связанные задачи недоступны" /> : null}
        {tab.kind === 'map' ? snapshot?.lat != null && snapshot.lon != null ? <>
          <Button variant="secondary" aria-pressed={follow} onClick={() => setFollow(!follow)}>{follow ? 'Слежение включено' : 'Следовать за роботом'}</Button>
          <InspectionMap lat={snapshot.lat} lon={snapshot.lon} follow={follow} onUserPan={() => setFollow(false)} />
        </> : <EmptyState title="Координаты не получены" /> : null}
        {tab.kind === 'telemetry' ? <dl className="rp-check-telemetry">
          {([['Скорость', snapshot?.speed, 'м/с'], ['Заряд', snapshot?.charge_percent, '%'], ['Батарея 1', snapshot?.battery1_percent, '%'], ['Батарея 2', snapshot?.battery2_percent, '%'], ['Диск', snapshot?.disk_percent, '%'], ['Режим', formatRobotMode(snapshot?.mode)], ['ICP', snapshot?.icp_label], ['LTE', snapshot?.lte_label], ['Соединение', snapshot?.connection === 'wire' ? 'Проводное' : snapshot?.connection === 'lte' ? 'Мобильное' : null]] as const).map(([label, value, unit]) => <div key={label}><dt>{label}</dt><dd>{value == null ? 'Нет данных' : `${value}${unit ? ` ${unit}` : ''}`}</dd></div>)}
        </dl> : null}
        {tab.kind === 'scheme' ? snapshot ? <div ref={setSchemeHost} className="rp-check-scheme-host" /> : <EmptyState title="Данные диагностики не получены" /> : null}
        {tab.kind === 'section' ? <>
          {hasSectionReadings ? <div ref={setSchemeHost} className="rp-check-scheme-host" />
            : snapshot ? <p role="status">Настроенные показания для раздела недоступны.</p> : null}
          {sectionError ? <CheckError failure={sectionError} user={user} onRetry={refresh} /> : null}
          <details className="rp-check-supplementary"><summary>Технические данные</summary>
            {section ? section.fields.length ? section.fields.map((field, index) => <div className="rp-check-field" key={`${field.label}-${index}`}><h3>{field.label}</h3><pre className="rp-check-field-lines">{field.lines.length ? field.lines.join('\n') : 'Нет данных'}</pre></div>) : <EmptyState title="В разделе пока нет данных" />
              : !sectionError ? online ? <LoadingState label="Загружаем раздел" /> : <EmptyState title="Раздел ещё не загружен" /> : null}
          </details>
        </> : null}
      </section>
    </div>
  return <div className="rp-check-workspace" data-unified={Boolean(renderSummary)}>
    <div className="rp-check-layout classic-robot-layout" data-testid="robot-check-layout">
      <div className="rp-check-layout__overview" data-robot-overview>{identity}</div>
      <div className="rp-check-layout__details" data-robot-details>{detail}</div>
    </div>
  </div>
}
export function RobotCheckWorkspace(props: RobotCheckWorkspaceProps) {
  return <RobotCheckController key={`${props.user.id}:${props.vin}:${checkAccessIdentity(props.user)}`} {...props} />
}
