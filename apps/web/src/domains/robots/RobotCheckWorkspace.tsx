import { useCallback, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { api, type EmergencySection, type EmergencySectionDetail, type EmergencySnapshot } from '../../api'
import { canAccessRoute, type AccessUser } from '../../app/routing/accessPolicy'
import { InspectionMap } from '../../components/emergency/InspectionMap'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, ErrorState, LoadingState } from '../../design-system/feedback/AsyncState'
import type { DomainError } from '../../shared/api/classifyApiError'
import { useOnlineStatus } from '../../shared/browser/useOnlineStatus'
import { RobotCheckSummary } from './RobotCheckSummary'
import { RobotCheckTabs } from './RobotCheckTabs'
import { RobotDiagnosticDiagram } from './RobotDiagnosticDiagram'
import { checkAccessIdentity, checkTabs, classifyCheckError } from './robotCheckUrl'
import { useVisibilityPolling } from './useVisibilityPolling'
import './robot-check.css'

export type RobotCheckApiClient = Pick<typeof api, 'emergencySnapshot' | 'emergencySection'>
export type RobotCheckWorkspaceProps = {
  vin: string; user: AccessUser; sections: EmergencySection[]; activeTab: string
  onTabChange: (tab: string) => void; apiClient?: RobotCheckApiClient
  onAuthorizationFailure?: (failure: DomainError) => void
  renderSummary?: (snapshot: EmergencySnapshot, failure: DomainError | null, refresh: () => void) => ReactNode
}
export function CheckError({ failure, user, onRetry }: { failure: DomainError; user: AccessUser; onRetry?: () => void }) {
  return <><ErrorState {...failure} onRetry={failure.retryable ? onRetry : undefined} />
    {failure.kind === 'configuration' && canAccessRoute(user, 'admin-robot-check') ? <Link to="/admin/emergency/config">Открыть настройки</Link> : null}</>
}
function WorkspaceOwner({ vin, user, sections, activeTab, onTabChange, apiClient = api, onAuthorizationFailure, renderSummary }: RobotCheckWorkspaceProps) {
  const online = useOnlineStatus()
  const [snapshot, setSnapshot] = useState<EmergencySnapshot | null>(null)
  const [snapshotError, setSnapshotError] = useState<DomainError | null>(null)
  const [details, setDetails] = useState<Record<string, EmergencySectionDetail>>({})
  const [errors, setErrors] = useState<Record<string, DomainError | null>>({})
  const [denied, setDenied] = useState<DomainError | null>(null)
  const [follow, setFollow] = useState(true)
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
  const task = useCallback(async () => {
    const requestedGeneration = generation.current
    const current = () => requestedGeneration === generation.current && !denial.current
    const observeFailure = (error: unknown, section?: string) => {
      if (!current()) return
      const failure = classifyCheckError(error)
      if (failure.kind === 'unauthorized' || failure.kind === 'forbidden') {
        denial.current = true
        setSnapshot(null); setDetails({}); setErrors({}); setSnapshotError(null); setDenied(failure)
        notify.current?.(failure)
      } else if (section) setErrors(previous => ({ ...previous, [section]: failure }))
      else setSnapshotError(failure)
    }
    if (!current()) return
    // Observe both independently: denial cannot wait for a hung sibling.
    const snapshotRequest = Promise.resolve().then(() => apiClient.emergencySnapshot(vin)).then(value => {
      if (current()) { setSnapshot(value); setSnapshotError(null) }
    }, error => { observeFailure(error); throw error })
    const requests = [snapshotRequest]
    if (tab.kind === 'section') requests.push(Promise.resolve().then(() => apiClient.emergencySection(vin, tab.id)).then(value => {
      if (current()) { setDetails(previous => ({ ...previous, [tab.id]: value })); setErrors(previous => ({ ...previous, [tab.id]: null })) }
    }, error => { observeFailure(error, tab.id); throw error }))
    const results = await Promise.allSettled(requests)
    const rejected = results.find(result => result.status === 'rejected')
    if (rejected?.status === 'rejected') throw rejected.reason
  }, [vin, tab.id, tab.kind, apiClient])
  const { pending, refreshNow } = useVisibilityPolling({ enabled: !denied, online, task })
  const refresh = () => { void refreshNow() }
  if (denied) return <CheckError failure={denied} user={user} />
  const section = details[tab.id]
  const sectionError = errors[tab.id]
  const retainSnapshot = !snapshotError || ['offline', 'timeout', 'server'].includes(snapshotError.kind)
  if (renderSummary && snapshotError && (!snapshot || !retainSnapshot)) return <>
    <CheckError failure={snapshotError} user={user} onRetry={refresh} />
    {snapshotError.kind === 'not-found' ? <Link to="/robots">К поиску роботов</Link> : null}
  </>
  return <div className="rp-check-workspace" data-unified={Boolean(renderSummary)}>
    <div className="rp-check-first-level">
      {snapshot ? renderSummary ? renderSummary(snapshot, snapshotError, refresh) : <RobotCheckSummary snapshot={snapshot} online={online} failed={Boolean(snapshotError)} pending={pending} onRefresh={refresh} />
        : <section className="rp-check-summary" aria-busy={pending}>
          {!online ? <p role="status">Нет сети на этом устройстве</p> : null}
          {!snapshotError && online ? <LoadingState label="Загружаем данные робота" /> : null}
          <Button leadingIcon="refresh" onClick={refresh}>{!online ? 'Повторить проверку' : 'Обновить данные'}</Button>
        </section>}
      {snapshotError && !renderSummary ? <div className="rp-check-warning"><CheckError failure={snapshotError} user={user} />{snapshot ? <p>Показаны последние полученные данные.</p> : null}</div> : null}
    </div>
    <div className="rp-check-detail">
      <RobotCheckTabs tabs={tabs} activeId={tab.id} onChange={onTabChange} />
      <section className="rp-check-panel" role="tabpanel" tabIndex={0} id={`robot-check-panel-${tab.id}`} aria-labelledby={`robot-check-tab-${tab.id}`}>
        {tab.kind === 'map' ? snapshot?.lat != null && snapshot.lon != null ? <>
          <Button variant="secondary" aria-pressed={follow} onClick={() => setFollow(!follow)}>{follow ? 'Слежение включено' : 'Следовать за роботом'}</Button>
          <InspectionMap lat={snapshot.lat} lon={snapshot.lon} follow={follow} onUserPan={() => setFollow(false)} />
        </> : <EmptyState title="Координаты не получены" /> : null}
        {tab.kind === 'telemetry' ? <dl className="rp-check-telemetry">
          {([['Скорость', snapshot?.speed, 'м/с'], ['Заряд', snapshot?.charge_percent, '%'], ['Батарея 1', snapshot?.battery1_percent, '%'], ['Батарея 2', snapshot?.battery2_percent, '%'], ['Диск', snapshot?.disk_percent, '%'], ['Режим', snapshot?.mode], ['ICP', snapshot?.icp_label], ['LTE', snapshot?.lte_label], ['Соединение', snapshot?.connection === 'wire' ? 'Проводное' : snapshot?.connection === 'lte' ? 'Мобильное' : null]] as const).map(([label, value, unit]) => <div key={label}><dt>{label}</dt><dd>{value == null ? 'Нет данных' : `${value}${unit ? ` ${unit}` : ''}`}</dd></div>)}
        </dl> : null}
        {tab.kind === 'scheme' ? snapshot ? <RobotDiagnosticDiagram faults={snapshot.wheels_fault} onSelectWheels={tabs.some(item => item.id === 'wheels' && item.kind === 'section') ? () => onTabChange('wheels') : undefined} /> : <EmptyState title="Данные диагностики не получены" /> : null}
        {tab.kind === 'section' ? <>
          {sectionError ? <CheckError failure={sectionError} user={user} onRetry={refresh} /> : null}
          {section ? section.fields.length ? section.fields.map((field, index) => <div className="rp-check-field" key={`${field.label}-${index}`}><h3>{field.label}</h3><pre className="rp-check-field-lines">{field.lines.length ? field.lines.join('\n') : 'Нет данных'}</pre></div>) : <EmptyState title="В разделе пока нет данных" />
            : !sectionError ? online ? <LoadingState label="Загружаем раздел" /> : <EmptyState title="Раздел ещё не загружен" /> : null}
        </> : null}
      </section>
    </div>
  </div>
}
export function RobotCheckWorkspace(props: RobotCheckWorkspaceProps) {
  return <WorkspaceOwner key={`${props.vin}:${checkAccessIdentity(props.user)}`} {...props} />
}
