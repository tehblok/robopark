import { Button } from '../../design-system/actions/Button'
import { OpsAlert } from './OpsAlert'
import { type FormEvent, useEffect, useRef, useState } from 'react'
import { api, ApiError, type OpsJob, type UpdateInspection } from '../../api'
import { mapApiError } from '../../i18n/errors'
import { ru } from '../../i18n/ru'
import { LoadingState } from '../../design-system/feedback/AsyncState'
import { Dialog } from '../../design-system/overlays/Dialog'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { Panel } from '../PageShell'
import { SystemHealthPanel } from './SystemHealthPanel'
import { SystemVersionPanel } from './SystemVersionPanel'
import { age, opsText, repairLabels, updateProgress, useOpsResource } from './opsPresentation'
import { activeJob, useOpsJob } from './useOpsJob'
import './AdminOpsPanel.css'

const CONFIRM = 'ОБНОВИТЬ'
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
  const { job, accept, error: pollingError, refresh: refreshJob } = useOpsJob()
  const available = useOpsResource('ops:available-update', api.opsAvailableUpdate)
  const releaseStatus = useOpsResource('ops:release-status', api.opsReleaseStatus)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const actionLock = useRef(false)
  const [healthRevision, setHealthRevision] = useState(0)
  const [restoreFile, setRestoreFile] = useState<File | null>(null)
  const [restoreConfirm, setRestoreConfirm] = useState('')
  const [inspection, setInspection] = useState<UpdateInspection | null>(null)
  const [inspecting, setInspecting] = useState(false)
  const inspectionRequest = useRef(0)
  const [updateConfirm, setUpdateConfirm] = useState('')
  const [githubOpen, setGithubOpen] = useState(false)
  const [githubConfirm, setGithubConfirm] = useState('')
  const githubInput = useRef<HTMLInputElement>(null)
  const [selectedRelease, setSelectedRelease] = useState<NonNullable<NonNullable<typeof available.data>['release']> | null>(null)
  const active = activeJob(job)
  const blocked = busy || active
  const completed = job?.state === 'succeeded'
  const progress = job ? updateProgress(job) : null
  const { refresh } = available
  const previousJob = useRef<OpsJob | null>(null)

  useEffect(() => () => { inspectionRequest.current++ }, [])
  useEffect(() => {
    const wasActive = activeJob(previousJob.current)
    previousJob.current = job
    if (wasActive && (job?.state === 'succeeded' || job?.state === 'failed')) {
      setHealthRevision(value => value + 1)
      void refresh()
    }
  }, [job, refresh])

  const run = async (action: () => Promise<OpsJob>) => {
    if (actionLock.current || active) return false
    actionLock.current = true
    setError(''); setBusy(true)
    try {
      const next = await action()
      accept(next)
      if (next.state === 'succeeded' || next.state === 'failed') {
        setHealthRevision(value => value + 1)
        void refresh()
      }
      return true
    }
    catch (caught) {
      setError(mapApiError(caught, ru.errors.generic))
      if (caught instanceof ApiError && caught.status === 409) {
        setHealthRevision(value => value + 1)
        await Promise.allSettled([refresh(), refreshJob()])
      }
      return false
    }
    finally { actionLock.current = false; setBusy(false) }
  }
  const abort = async () => {
    if (actionLock.current) return
    actionLock.current = true
    setError(''); setBusy(true)
    try {
      accept(await api.opsAbort())
      setHealthRevision(value => value + 1)
      await Promise.allSettled([refresh(), refreshJob()])
    } catch (caught) {
      setError(mapApiError(caught, ru.errors.generic))
    } finally {
      actionLock.current = false
      setBusy(false)
    }
  }
  const getArtifact = async (kind: 'snapshot' | 'diagnostics') => {
    if (job?.kind !== kind || !completed || !job.artifact_ready || blocked) return
    setError('')
    setBusy(true)
    try { await download(await (kind === 'diagnostics' ? api.opsDiagnosticArtifact() : api.opsArtifact()), `robopark-${kind}.zip`) }
    catch (caught) { setError(mapApiError(caught, ru.errors.generic)) }
    finally { setBusy(false) }
  }
  const inspect = async (file: File | null) => {
    const request = ++inspectionRequest.current
    setInspection(null); setUpdateConfirm(''); setError(''); setInspecting(Boolean(file))
    if (!file) return
    try { const result = await api.opsInspectUpdate(file); if (request === inspectionRequest.current) setInspection(result) }
    catch (caught) { if (request === inspectionRequest.current) setError(mapApiError(caught, ru.errors.generic)) }
    finally { if (request === inspectionRequest.current) setInspecting(false) }
  }
  const update = async (event: FormEvent) => {
    event.preventDefault()
    if (!inspection || inspecting || updateConfirm !== CONFIRM) return
    const installed = await run(() => api.opsApproveUpdate(inspection.inspection_id, CONFIRM))
    if (installed) { setInspection(null); setUpdateConfirm('') }
  }
  const approveGithub = async () => {
    if (!selectedRelease || githubConfirm !== CONFIRM || available.data?.state !== 'available' || available.error || available.isRevalidating || available.data.release?.release_id !== selectedRelease.release_id) return
    const ok = await run(async () => {
      try { return await api.opsApproveGithubUpdate(selectedRelease.release_id, CONFIRM) }
      catch (caught) {
        if (caught instanceof ApiError && (caught.status === 409 || caught.status === 404 || caught.detail === 'github_release_unavailable')) {
          setGithubOpen(false); setGithubConfirm(''); if (caught.status !== 409) await refresh()
        }
        throw caught
      }
    })
    if (ok) { setGithubOpen(false); setGithubConfirm(''); void refresh() }
  }

  return <div className="ops-stack">
    {error && <OpsAlert tone="error">{error}</OpsAlert>}
    {releaseStatus.data && <SystemVersionPanel value={releaseStatus.data} />}
    <SystemHealthPanel onRepair={() => void run(api.opsRepair)} busy={blocked || inspecting} revision={healthRevision} />
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
      {active && <div className="form-actions"><Button variant="secondary" disabled={busy} onClick={() => void abort()} type="button">Прервать и снять техработы</Button></div>}
      {job.error && <OpsAlert tone="error">{mapApiError(new ApiError(400, job.error), 'Операция не завершена. Подробности доступны в диагностике.')}</OpsAlert>}
      {job.phase === 'rolling_back' && <OpsAlert>Новая версия не прошла проверку. Восстанавливается предыдущая версия.</OpsAlert>}
      {job.restart_required && completed && <OpsAlert>Ожидаем автоматический перезапуск приложения на хосте.</OpsAlert>}
      {job.host_result && <ul className="ops-checks">
        {job.host_result.performed.map(action => <li key={`done-${action}`}><StatusBadge tone="success">Выполнено: {repairLabels[action] ?? 'разрешённое исправление'}</StatusBadge></li>)}
        {job.host_result.failed.map(action => <li key={`failed-${action}`}><StatusBadge tone="critical">Не выполнено: {repairLabels[action] ?? 'разрешённое исправление'}</StatusBadge></li>)}
        {job.kind === 'repair' && !job.host_result.performed.length && !job.host_result.failed.length && <li>Исправления не потребовались.</li>}
      </ul>}
    </Panel> : <p className="muted">{ru.ops.jobIdle}</p>}

    <Panel title="Диагностика" hint="Соберите отчёт для разбора состояния системы.">
      <div className="form-actions">
        <Button disabled={blocked || inspecting} onClick={() => void run(api.opsDiagnostics)} type="button">Собрать диагностику</Button>
        <Button variant="secondary" disabled={blocked || job?.kind !== 'diagnostics' || !completed || !job.artifact_ready} onClick={() => void getArtifact('diagnostics')} type="button">Скачать диагностику</Button>
      </div>
    </Panel>

    <Panel title="Обновление из GitHub" hint="Показываются только релизы, проверенные хостом.">
      {!available.data && !available.error && <LoadingState label="Проверяем доступность обновления" />}
      {available.error ? <OpsAlert>Проверка обновлений временно недоступна. Показаны последние известные сведения.</OpsAlert> : null}
      {available.data && <>
        <p>{discoveryLabels[available.data.state]}</p>
        <p className="muted">Проверено: {age(available.data.checked_at)}</p>
        {available.data.release && <div className="ops-release-details">
          <p>Версия {opsText(available.data.release.version)}</p>
          <p>Размер: {(available.data.release.size / 1048576).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} МБ · Git {opsText(available.data.release.git_sha.slice(0, 7))}</p>
          <p>SHA-256: {opsText(available.data.release.sha256)}</p>
        </div>}
        {available.data.state === 'available' && available.data.release && <div className="form-actions"><Button type="button" disabled={blocked || inspecting || Boolean(available.error) || available.isRevalidating} onClick={() => { setSelectedRelease(available.data!.release); setGithubConfirm(''); setGithubOpen(true) }}>Обновить из GitHub</Button></div>}
      </>}
    </Panel>
    <Dialog open={githubOpen} onOpenChange={setGithubOpen} title="Подтвердить обновление" description="На время установки система перейдёт в режим технических работ. Хост проверит новую версию и при сбое выполнит откат." initialFocusRef={githubInput} dismissible={!busy}>
      <p>Версия {opsText(selectedRelease?.version)}</p>
      <label className="field"><span className="field-label">Для GitHub введите ОБНОВИТЬ</span><input ref={githubInput} autoComplete="off" value={githubConfirm} onChange={event => setGithubConfirm(event.target.value)} disabled={busy} /></label>
      <div className="form-actions"><Button type="button" disabled={blocked || githubConfirm !== CONFIRM || available.data?.state !== 'available' || Boolean(available.error) || available.isRevalidating || available.data.release?.release_id !== selectedRelease?.release_id} onClick={() => void approveGithub()}>Установить версию {opsText(selectedRelease?.version)}</Button></div>
    </Dialog>

    <Panel collapsible storageKey="admin-ops-update" hint={ru.ops.updateHint} title={ru.ops.updateTitle}>
      <form className="form-grid" onSubmit={event => void update(event)}>
        <label className="field"><span className="field-label">Архив обновления</span><input accept=".zip,application/zip" disabled={blocked} onChange={event => void inspect(event.target.files?.[0] ?? null)} type="file" /></label>
        {inspecting && <LoadingState label="Проверяем архив обновления" />}
        {inspection && <div className="ops-release-details" aria-live="polite">
          <p>Версия {opsText(inspection.version)}</p><p>Git {opsText(inspection.git_sha.slice(0, 7))}</p><p>Миграция: {opsText(inspection.migration_head)}</p><p>{opsText(inspection.notes, 'Описание изменений не указано')}</p>
        </div>}
        <label className="field"><span className="field-label">Для установки введите ОБНОВИТЬ</span><input autoComplete="off" disabled={blocked || !inspection || inspecting} onChange={event => setUpdateConfirm(event.target.value)} value={updateConfirm} /></label>
        <div className="form-actions"><Button disabled={blocked || inspecting || !inspection || updateConfirm !== CONFIRM} type="submit">Установить обновление</Button></div>
      </form>
    </Panel>
    <Panel hint={ru.ops.snapshotHint} title={ru.ops.snapshotTitle}>
      <p className="muted">{ru.ops.secretKeyHint}</p>
      <div className="form-actions"><Button disabled={blocked || inspecting} onClick={() => void run(api.opsSnapshot)} type="button">{ru.ops.snapshotStart}</Button><Button variant="secondary" disabled={blocked || job?.kind !== 'snapshot' || !completed || !job.artifact_ready} onClick={() => void getArtifact('snapshot')} type="button">{ru.ops.snapshotDownload}</Button></div>
    </Panel>
    <Panel hint={ru.ops.restoreHint} title={ru.ops.restoreTitle}>
      <form className="form-grid" onSubmit={event => { event.preventDefault(); if (restoreFile && restoreConfirm === 'ВОССТАНОВИТЬ') void run(() => api.opsRestore(restoreFile, restoreConfirm)) }}>
        <label className="field"><span className="field-label">Архив восстановления</span><input accept=".zip,application/zip" disabled={blocked || inspecting} onChange={event => { setRestoreFile(event.target.files?.[0] ?? null); setRestoreConfirm('') }} type="file" /></label>
        <label className="field"><span className="field-label">Для восстановления введите ВОССТАНОВИТЬ</span><input autoComplete="off" disabled={blocked || inspecting} onChange={event => setRestoreConfirm(event.target.value)} value={restoreConfirm} /></label>
        <div className="form-actions"><Button variant="danger" disabled={blocked || inspecting || !restoreFile || restoreConfirm !== 'ВОССТАНОВИТЬ'} type="submit">{ru.ops.restoreSubmit}</Button></div>
      </form>
    </Panel>
  </div>
}
