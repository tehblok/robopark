import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../../../api'
import { Alert, Panel } from '../../../components/PageShell'
import { Button } from '../../../design-system/actions/Button'
import { StatusBadge } from '../../../design-system/status/StatusBadge'
import { otaUploadClient, type HostCapabilities, type OtaUploadClient, type SystemClient, type SystemJob } from '../../../opsApi'
import { requestServiceWorkerUpdate } from '../../../pwa/registerServiceWorker'
import { clearOperationReservation, safeOperationDraft, writeOperationReservation, type OperationActor } from '../operationReservation'
import { inspectOtaFile } from './otaManifest'
import type { OtaManifestPreview } from './otaTypes'
import { hashOtaFile, uploadOtaFile } from './otaUpload'
import './OtaUpdatePanel.css'

type Stage = 'idle' | 'checking' | 'ready' | 'uploading' | 'uploaded' | 'starting' | 'error'
const CONFIRMATION = 'UPDATE ROBOPARK'
const bytes = (value: number) => value < 1024 * 1024 ? `${Math.ceil(value / 1024)} КиБ` : `${(value / 1024 / 1024).toFixed(1)} МиБ`

export function OtaUpdatePanel({ actor, system, uploads = otaUploadClient, capabilities, currentBuildId, job, onAccepted, onPostingChange, onRefreshCapabilities }: {
  actor: OperationActor
  system: SystemClient
  uploads?: OtaUploadClient
  capabilities: HostCapabilities
  currentBuildId?: string | null
  job: SystemJob | null
  onAccepted: (job: SystemJob) => void
  onPostingChange: (operationId: string | null) => void
  onRefreshCapabilities: () => Promise<void>
}) {
  const [file, setFile] = useState<File | null>(null)
  const [manifest, setManifest] = useState<OtaManifestPreview | null>(null)
  const [sha256, setSha256] = useState('')
  const [stage, setStage] = useState<Stage>('idle')
  const [progress, setProgress] = useState(0)
  const [uploadId, setUploadId] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [error, setError] = useState('')
  const [fresh, setFresh] = useState(false)
  const initialBuild = useRef(currentBuildId)
  const refreshedBuild = useRef<string | null>(null)
  const active = job?.kind === 'ota-update' && (job.state === 'queued' || job.state === 'running')
  const capability = capabilities.operations['ota-update']

  useEffect(() => {
    const expiresAt = Date.parse(capabilities.expires_at ?? '')
    const update = () => setFresh(capabilities.state === 'ready' && Boolean(capabilities.revision) && Number.isFinite(expiresAt) && expiresAt > Date.now())
    update()
    const remaining = expiresAt - Date.now()
    const timer = Number.isFinite(remaining) && remaining > 0 ? window.setTimeout(update, Math.min(remaining, 2_147_000_000)) : undefined
    return () => window.clearTimeout(timer)
  }, [capabilities.expires_at, capabilities.revision, capabilities.state])

  useEffect(() => {
    if (job?.kind === 'ota-update' && (job.receipt_state === 'terminal' || ['succeeded', 'failed'].includes(job.state))) clearOperationReservation(actor)
  }, [actor, job])
  useEffect(() => {
    if (job?.kind !== 'ota-update' || job.state !== 'succeeded' || !currentBuildId) return
    const before = sessionStorage.getItem(`robopark:ota-before-build:${job.id}`) ?? initialBuild.current
    const refreshKey = `robopark:ota-refreshed:${job.id}:${currentBuildId}`
    if (currentBuildId === before || refreshedBuild.current === currentBuildId || sessionStorage.getItem(refreshKey) === '1') return
    refreshedBuild.current = currentBuildId
    sessionStorage.setItem(refreshKey, '1')
    sessionStorage.removeItem(`robopark:ota-before-build:${job.id}`)
    void requestServiceWorkerUpdate().catch(() => {})
  }, [currentBuildId, job])

  const choose = async (next: File | null) => {
    setFile(next); setManifest(null); setSha256(''); setUploadId(''); setProgress(0); setError('')
    if (!next) { setStage('idle'); return }
    setStage('checking')
    try {
      const inspected = await inspectOtaFile(next)
      setManifest(inspected)
      const digest = await hashOtaFile(next, value => setProgress(value.total ? value.loaded / value.total * 100 : 0))
      setSha256(digest); setProgress(100); setStage('ready')
    } catch (caught) {
      setStage('error'); setError(caught instanceof Error ? caught.message : 'Не удалось проверить OTA-пакет.')
    }
  }

  const upload = async () => {
    if (!file || !sha256 || stage !== 'ready') return
    setError(''); setStage('uploading'); setProgress(0)
    try {
      const verified = await uploadOtaFile(file, sha256, uploads, offset => setProgress(file.size ? offset / file.size * 100 : 0))
      if (verified.version && manifest?.app_version !== verified.version) throw new Error('Версия пакета после серверной проверки не совпала.')
      setUploadId(verified.upload_id); setStage('uploaded'); setProgress(100)
    } catch (caught) {
      setStage('error'); setError(caught instanceof Error ? caught.message : 'Не удалось отправить OTA-пакет.')
    }
  }

  const install = async () => {
    const revision = fresh ? capabilities.revision : null
    if (!revision || !manifest || !uploadId || confirmation !== CONFIRMATION || !password || !code || active) return
    const operationId = crypto.randomUUID()
    const payload = {
      operation_id: operationId, kind: 'ota-update' as const, capability_revision: revision,
      confirmation, upload_id: uploadId, sha256, version: manifest.app_version,
    }
    setStage('starting'); setError('')
    try {
      if (currentBuildId) sessionStorage.setItem(`robopark:ota-before-build:${operationId}`, currentBuildId)
      writeOperationReservation(actor, { id: operationId, kind: 'ota-update', created_at: Date.now(), phase: 'posting', draft: safeOperationDraft(payload) })
      onPostingChange(operationId)
      const authorization = await system.reauthorize({ password, code, operation_kind: 'ota-update', operation_id: operationId, capability_revision: revision })
      try {
        const next = await system.startOperation(payload, authorization.token)
        writeOperationReservation(actor, { id: operationId, kind: 'ota-update', created_at: Date.now(), phase: 'reconciling', draft: safeOperationDraft(payload) })
        onAccepted(next)
      } catch {
        writeOperationReservation(actor, { id: operationId, kind: 'ota-update', created_at: Date.now(), phase: 'reconciling', draft: safeOperationDraft(payload) })
        onAccepted({ id: operationId, kind: 'ota-update', state: 'queued', phase: 'Проверяем получение запроса', progress_percent: 0, error: null })
      }
      setPassword(''); setCode(''); setConfirmation(''); setStage('uploaded')
    } catch (caught) {
      clearOperationReservation(actor)
      sessionStorage.removeItem(`robopark:ota-before-build:${operationId}`)
      if (caught instanceof ApiError && ['capabilities_changed', 'capability_unavailable', 'capabilities_unavailable'].includes(caught.detail ?? '')) {
        await onRefreshCapabilities(); setError('Возможности хоста изменились. Проверьте пакет и подтвердите установку заново.')
      } else setError('Не удалось подтвердить запуск. Проверьте пароль и одноразовый код.')
      setStage('uploaded')
    } finally { onPostingChange(null) }
  }

  return <section aria-label="OTA-обновление" className="rp-ota">
    <Panel title="Обновление системы" hint="Пакет сначала проверяется на этом устройстве, затем передаётся частями и повторно проверяется сервером.">
      {(!fresh || !capability?.available) && <Alert tone="warning">OTA сейчас недоступно на хосте. Выбор и проверка пакета доступны, установка заблокирована.</Alert>}
      {error && <Alert tone="error">{error}</Alert>}
      <div className="rp-ota-drop" onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); void choose(event.dataTransfer.files[0] ?? null) }}>
        <label><strong>{file ? file.name : 'Выберите OTA-пакет'}</strong><span>Файл .ota, до 2 ГБ. Проверка выполняется локально.</span><input accept=".ota,application/zip" disabled={active || stage === 'uploading' || stage === 'starting'} onChange={event => void choose(event.target.files?.[0] ?? null)} type="file" /></label>
      </div>
      {(stage === 'checking' || stage === 'uploading') && <div className="rp-ota-progress"><span>{stage === 'checking' ? 'Проверка SHA‑256' : 'Передача на сервер'} · {Math.round(progress)}%</span><progress max="100" value={progress} /></div>}
      {manifest && sha256 && <div className="rp-ota-preview">
        <div><span>Версия</span><strong>{manifest.app_version}</strong></div><div><span>Размер</span><strong>{file ? bytes(file.size) : '—'}</strong></div>
        <div><span>Свободное место</span><strong>{bytes(manifest.required_free_bytes)}</strong></div><div><span>SHA‑256</span><code>{sha256}</code></div>
        {manifest.compatible_from.length > 0 && <div className="rp-ota-wide"><span>Совместимо с</span><strong>{manifest.compatible_from.join(', ')}</strong></div>}
        {manifest.changes.length > 0 && <details className="rp-ota-wide"><summary>Что изменится</summary><ul>{manifest.changes.map((change, index) => <li key={`${index}:${change}`}>{change}</li>)}</ul></details>}
      </div>}
      {stage === 'ready' && <Button disabled={!fresh || !capability?.available} onClick={() => void upload()} type="button">Проверить на сервере</Button>}
      {stage === 'uploaded' && !active && <div className="rp-ota-confirm">
        <label className="field"><span className="field-label">Введите {CONFIRMATION}</span><input autoComplete="off" onChange={event => setConfirmation(event.target.value)} value={confirmation} /></label>
        <label className="field"><span className="field-label">Пароль</span><input autoComplete="current-password" onChange={event => setPassword(event.target.value)} type="password" value={password} /></label>
        <label className="field"><span className="field-label">Код TOTP или восстановления</span><input autoComplete="one-time-code" onChange={event => setCode(event.target.value)} value={code} /></label>
        <Button disabled={!fresh || confirmation !== CONFIRMATION || !password || !code} onClick={() => void install()} type="button">Установить {manifest?.app_version}</Button>
      </div>}
      {job?.kind === 'ota-update' && <div className="rp-ota-job">
        <StatusBadge tone={job.state === 'failed' ? 'critical' : job.state === 'succeeded' ? 'success' : 'info'}>{job.phase || job.state}</StatusBadge>
        <progress aria-label="Прогресс OTA-обновления" max="100" value={job.progress_percent ?? (['succeeded', 'failed'].includes(job.state) ? 100 : 0)} />
        {job.error && <Alert tone="error">Обновление завершилось ошибкой: {job.error}. Система выполнила автоматический откат.</Alert>}
      </div>}
    </Panel>
  </section>
}
