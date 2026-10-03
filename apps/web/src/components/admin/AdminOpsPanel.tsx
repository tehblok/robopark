import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, ApiError, type OpsJob } from '../../api'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { mapApiError } from '../../i18n/errors'
import { ru } from '../../i18n/ru'
import { Panel } from '../PageShell'
import { OpsAlert } from './OpsAlert'
import { SystemHealthPanel } from './SystemHealthPanel'
import { SystemVersionPanel } from './SystemVersionPanel'
import { activeJob, useOpsJob } from './useOpsJob'
import { repairLabels, updateProgress, useOpsResource } from './opsPresentation'
import './AdminOpsPanel.css'

const jobKinds: Record<string, string> = { snapshot: 'Снимок', restore: 'Восстановление', update: 'Обновление', diagnostics: 'Диагностика', repair: 'Исправление' }
const jobStates: Record<string, string> = { queued: 'В очереди', running: 'Выполняется', succeeded: 'Завершено', failed: 'Не удалось завершить' }
const phases: Record<string, string> = { awaiting_host: 'Ожидаем хост', host_dispatched: 'Операция передана хосту', building_snapshot: 'Создаём снимок', validating: 'Проверяем архив', replacing_data: 'Восстанавливаем данные', testing: 'Проверяем новую версию', pre_cutover_snapshot: 'Создаём резервную копию', awaiting_rebuild: 'Ожидаем запуска новой версии', applying: 'Устанавливаем версию', rolling_back: 'Восстанавливаем предыдущую версию' }
export function AdminOpsPanel() {
  const { job, error: pollingError } = useOpsJob()
  const releaseStatus = useOpsResource('ops:release-status', api.opsReleaseStatus)
  const [healthRevision, setHealthRevision] = useState(0)
  const [params] = useSearchParams()
  const previousJob = useRef<OpsJob | null>(null)
  const active = activeJob(job)
  const completed = job?.state === 'succeeded'
  const progress = job ? updateProgress(job) : null
  const park = params.get('park')
  const systemHref = `/system${park ? `?park=${encodeURIComponent(park)}` : ''}#system-operations`

  useEffect(() => {
    const wasActive = activeJob(previousJob.current)
    previousJob.current = job
    if (wasActive && (job?.state === 'succeeded' || job?.state === 'failed')) {
      setHealthRevision(value => value + 1)
    }
  }, [job])

  return <div className="ops-stack">
    {releaseStatus.data && <SystemVersionPanel value={releaseStatus.data} />}
    <SystemHealthPanel revision={healthRevision} />
    {pollingError && <OpsAlert>Связь с хостом временно потеряна. Последний статус сохранён; проверка продолжится автоматически.</OpsAlert>}
    {job && job.id && job.state !== 'idle' ? <Panel density="dense" title="Текущая операция">
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
    <Panel density="dense" title="Системные операции" hint="Диагностика, обновления и их артефакты доступны через проверенный контракт возможностей хоста.">
      <div className="form-actions"><Link className="btn btn-secondary" to={systemHref}>Открыть системные операции</Link></div>
    </Panel>
  </div>
}
