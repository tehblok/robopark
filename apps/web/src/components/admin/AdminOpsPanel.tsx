import { useEffect, useRef, useState } from 'react'
import { api, ApiError, type OpsJob } from '../../api'
import { Button } from '../../design-system/actions/Button'
import { LoadingState } from '../../design-system/feedback/AsyncState'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { mapApiError } from '../../i18n/errors'
import { ru } from '../../i18n/ru'
import { Panel } from '../PageShell'
import { OpsAlert } from './OpsAlert'
import { SystemHealthPanel } from './SystemHealthPanel'
import { SystemVersionPanel } from './SystemVersionPanel'
import { activeJob, useOpsJob } from './useOpsJob'
import { age, opsText, repairLabels, updateProgress, useOpsResource } from './opsPresentation'
import './AdminOpsPanel.css'

const jobKinds: Record<string, string> = { snapshot: 'Снимок', restore: 'Восстановление', update: 'Обновление', diagnostics: 'Диагностика', repair: 'Исправление' }
const jobStates: Record<string, string> = { queued: 'В очереди', running: 'Выполняется', succeeded: 'Завершено', failed: 'Не удалось завершить' }
const phases: Record<string, string> = { awaiting_host: 'Ожидаем хост', host_dispatched: 'Операция передана хосту', building_snapshot: 'Создаём снимок', validating: 'Проверяем архив', replacing_data: 'Восстанавливаем данные', testing: 'Проверяем новую версию', pre_cutover_snapshot: 'Создаём резервную копию', awaiting_rebuild: 'Ожидаем запуска новой версии', applying: 'Устанавливаем версию', rolling_back: 'Восстанавливаем предыдущую версию' }
const discoveryLabels = { available: 'Доступна новая версия', up_to_date: 'Установлена актуальная версия', discovery_stale: 'Сведения об обновлении устарели. Ожидаем проверку хоста.', disabled: 'Проверка обновлений отключена на хосте', manual: 'Автопоиск отключён: доступны локальные обновления', approved: 'Обновление уже подтверждено' }

async function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

export function AdminOpsPanel() {
  const { job, error: pollingError } = useOpsJob()
  const available = useOpsResource('ops:available-update', api.opsAvailableUpdate)
  const releaseStatus = useOpsResource('ops:release-status', api.opsReleaseStatus)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [healthRevision, setHealthRevision] = useState(0)
  const previousJob = useRef<OpsJob | null>(null)
  const active = activeJob(job)
  const completed = job?.state === 'succeeded'
  const progress = job ? updateProgress(job) : null
  const { refresh } = available

  useEffect(() => {
    const wasActive = activeJob(previousJob.current)
    previousJob.current = job
    if (wasActive && (job?.state === 'succeeded' || job?.state === 'failed')) {
      setHealthRevision(value => value + 1)
      void refresh()
    }
  }, [job, refresh])

  const getDiagnosticArtifact = async () => {
    if (job?.kind !== 'diagnostics' || !completed || !job.artifact_ready || busy) return
    setError('')
    setBusy(true)
    try { await download(await api.opsDiagnosticArtifact(), 'robopark-diagnostics.zip') }
    catch (caught) { setError(mapApiError(caught, ru.errors.generic)) }
    finally { setBusy(false) }
  }

  return <div className="ops-stack">
    {error && <OpsAlert tone="error">{error}</OpsAlert>}
    {releaseStatus.data && <SystemVersionPanel value={releaseStatus.data} />}
    <SystemHealthPanel revision={healthRevision} />
    {pollingError && <OpsAlert>Связь с хостом временно потеряна. Последний статус сохранён; проверка продолжится автоматически.</OpsAlert>}
    {job && job.id && job.state !== 'idle' ? <Panel title="Текущая операция">
      <div aria-live="polite" aria-atomic="true" className="ops-health-summary">
        <span>{jobKinds[job.kind] ?? 'Операция хоста'}</span>
        <StatusBadge tone={completed ? 'success' : job.state === 'failed' ? 'critical' : 'info'}>{jobStates[job.state] ?? 'Состояние уточняется'}</StatusBadge>
        {active && <span>{phases[job.phase] ?? 'Хост выполняет операцию'}</span>}
      </div>
      {progress ? <div aria-live="polite" className="ops-update-progress" data-rollback={progress.rollback}>
        <div><span>{progress.label}</span><strong>{progress.percent}%</strong></div>
        <progress aria-label="Прогресс обновления" max={100} value={progress.percent} />
      </div> : null}
      {active && <OpsAlert tone="info">Операция выполняется хостом. Статус обновляется автоматически.</OpsAlert>}
      {job.error && <OpsAlert tone="error">{mapApiError(new ApiError(400, job.error), 'Операция не завершена. Подробности доступны в диагностике.')}</OpsAlert>}
      {job.phase === 'rolling_back' && <OpsAlert>Новая версия не прошла проверку. Восстанавливается предыдущая версия.</OpsAlert>}
      {job.restart_required && completed && <OpsAlert>Ожидаем автоматический перезапуск приложения на хосте.</OpsAlert>}
      {job.host_result && <ul className="ops-checks">
        {job.host_result.performed.map(action => <li key={`done-${action}`}><StatusBadge tone="success">Выполнено: {repairLabels[action] ?? 'разрешённое исправление'}</StatusBadge></li>)}
        {job.host_result.failed.map(action => <li key={`failed-${action}`}><StatusBadge tone="critical">Не выполнено: {repairLabels[action] ?? 'разрешённое исправление'}</StatusBadge></li>)}
      </ul>}
    </Panel> : <p className="muted">{ru.ops.jobIdle}</p>}
    <Panel title="Диагностика" hint="Новая операция запускается в разделе «Система» через проверенный контракт возможностей хоста.">
      <div className="form-actions"><Button variant="secondary" disabled={busy || job?.kind !== 'diagnostics' || !completed || !job.artifact_ready} onClick={() => void getDiagnosticArtifact()} type="button">Скачать диагностику</Button></div>
    </Panel>
    <Panel title="Доступность обновлений" hint="Этот экран только показывает сведения хоста. Управление выполняется в разделе «Система».">
      {!available.data && !available.error && <LoadingState label="Проверяем доступность обновления" />}
      {available.error ? <OpsAlert>Проверка обновлений временно недоступна. Показаны последние известные сведения.</OpsAlert> : null}
      {available.data && <>
        <p>{discoveryLabels[available.data.state]}</p>
        <p className="muted">Проверено: {age(available.data.checked_at)}</p>
        {available.data.release && <div className="ops-release-details"><p>Версия {opsText(available.data.release.version)}</p><p>Размер: {(available.data.release.size / 1048576).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} МБ · Git {opsText(available.data.release.git_sha.slice(0, 7))}</p><p>SHA-256: {opsText(available.data.release.sha256)}</p></div>}
      </>}
    </Panel>
  </div>
}
