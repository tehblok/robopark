import { useEffect, useRef, useState } from 'react'
import { ApiError } from '../../../api'
import { Alert, Panel } from '../../../components/PageShell'
import { Button } from '../../../design-system/actions/Button'
import { FileField } from '../../../design-system/inputs/FileField'
import { StatusBadge } from '../../../design-system/status/StatusBadge'
import { otaUploadClient, type HostCapabilities, type OtaUpload, type OtaUploadClient, type SystemClient, type SystemJob } from '../../../opsApi'
import { requestServiceWorkerUpdate } from '../../../pwa/registerServiceWorker'
import { clearOperationReservation, safeOperationDraft, writeOperationReservation, type OperationActor } from '../operationReservation'
import { inspectOtaFile } from './otaManifest'
import type { OtaManifestPreview } from './otaTypes'
import { hashOtaFile, uploadOtaFile } from './otaUpload'
import './OtaUpdatePanel.css'

type Stage = 'idle' | 'checking' | 'ready' | 'uploading' | 'uploaded' | 'removing' | 'starting' | 'error'
const CONFIRMATION = 'UPDATE ROBOPARK'
const bytes = (value: number) => value < 1024 * 1024
  ? `${Math.ceil(value / 1024)} КиБ`
  : value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(1)} ГиБ` : `${(value / 1024 ** 2).toFixed(1)} МиБ`

export function OtaUpdatePanel({ actor, system, uploads = otaUploadClient, capabilities, currentBuildId, currentVersion, job, onAccepted, onPostingChange, onRefreshCapabilities }: {
  actor: OperationActor
  system: SystemClient
  uploads?: OtaUploadClient
  capabilities: HostCapabilities
  currentBuildId?: string | null
  currentVersion?: string | null
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
  const [savedUploads, setSavedUploads] = useState<OtaUpload[] | null>(null)
  const [listing, setListing] = useState(false)
  const initialBuild = useRef(currentBuildId)
  const refreshedBuild = useRef<string | null>(null)
  const active = job?.kind === 'ota-update' && (job.state === 'queued' || job.state === 'running')
  const capability = capabilities.operations['ota-update']
  const busy = active || ['checking', 'uploading', 'removing', 'starting'].includes(stage)

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
    if (active || stage === 'uploading' || stage === 'removing' || stage === 'starting') return
    setFile(next); setManifest(null); setSha256(''); setUploadId(''); setProgress(0); setError('')
    if (!next) { setStage('idle'); return }
    setStage('checking')
    try {
      const inspected = await inspectOtaFile(next)
      setManifest(inspected)
      if (currentVersion && !inspected.compatible_from.includes(currentVersion)) {
        setStage('error')
        setError(inspected.compatible_from.length === 0
          ? 'Этот пакет только для чистой установки. Обновление существующей системы им невозможно.'
          : 'Этот пакет несовместим с установленной версией Robopark.')
        return
      }
      const digest = await hashOtaFile(next, value => setProgress(value.total ? value.loaded / value.total * 100 : 0))
      setSha256(digest); setProgress(100); setStage('ready')
    } catch (caught) {
      setStage('error'); setError(caught instanceof Error ? caught.message : 'Не удалось проверить OTA-пакет.')
    }
  }

  const upload = async () => {
    if (!file || !sha256 || stage !== 'ready' || (currentVersion && !manifest?.compatible_from.includes(currentVersion))) return
    setError(''); setStage('uploading'); setProgress(0)
    try {
      const verified = await uploadOtaFile(file, sha256, uploads, offset => setProgress(file.size ? offset / file.size * 100 : 0))
      if (verified.version && manifest?.app_version !== verified.version) throw new Error('Версия пакета после серверной проверки не совпала.')
      setUploadId(verified.upload_id); setStage('uploaded'); setProgress(100)
    } catch (caught) {
      setStage('ready')
      setError(caught instanceof ApiError && caught.detail === 'ota_upload_quota'
        ? 'Достигнут лимит загруженных пакетов. Нажмите «Показать мои загрузки» и удалите ненужный файл, затем повторите проверку. Если своих загрузок нет, дождитесь завершения загрузок других владельцев или автоматической очистки.'
        : caught instanceof Error ? caught.message : 'Не удалось отправить OTA-пакет.')
    }
  }

  const listUploads = async () => {
    if (busy || listing) return
    setListing(true); setError('')
    try { setSavedUploads((await uploads.list()).items) }
    catch { setError('Не удалось получить список загрузок. Проверьте соединение и повторите попытку.') }
    finally { setListing(false) }
  }

  const removeUpload = async (identity: string) => {
    if (busy || listing || !identity || (identity !== uploadId && !savedUploads?.some(row => row.upload_id === identity))) return
    const previousStage = stage
    setStage('removing'); setError('')
    setPassword(''); setCode(''); setConfirmation('')
    try {
      await uploads.remove(identity)
      setSavedUploads(current => current?.filter(row => row.upload_id !== identity) ?? null)
      if (identity === uploadId) { setUploadId(''); setStage('ready') }
      else setStage(previousStage)
    } catch (caught) {
      setStage(previousStage)
      setError(caught instanceof ApiError && caught.detail === 'ota_upload_in_use'
        ? 'Пакет уже используется системной операцией. Дождитесь её завершения.'
        : 'Не удалось удалить загруженный пакет. Проверьте соединение и повторите попытку.')
    }
  }

  const install = async () => {
    const revision = fresh ? capabilities.revision : null
    if (!revision || !manifest || !uploadId || confirmation !== CONFIRMATION || !password || !code || active
      || (currentVersion && !manifest.compatible_from.includes(currentVersion))) return
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
      } catch (caught) {
        if (caught instanceof ApiError && (
          (caught.status >= 400 && caught.status < 500 && caught.status !== 408 && caught.status !== 429)
          || (caught.status === 507 && caught.detail === 'ota_insufficient_space')
        )) throw caught
        writeOperationReservation(actor, { id: operationId, kind: 'ota-update', created_at: Date.now(), phase: 'reconciling', draft: safeOperationDraft(payload) })
        onAccepted({ id: operationId, kind: 'ota-update', state: 'queued', phase: 'Проверяем получение запроса', progress_percent: 0, error: null })
      }
      setPassword(''); setCode(''); setConfirmation(''); setStage('uploaded')
    } catch (caught) {
      clearOperationReservation(actor)
      sessionStorage.removeItem(`robopark:ota-before-build:${operationId}`)
      if (caught instanceof ApiError && ['capabilities_changed', 'capability_unavailable', 'capabilities_unavailable'].includes(caught.detail ?? '')) {
        await onRefreshCapabilities(); setError('Возможности хоста изменились. Проверьте пакет и подтвердите установку заново.')
      } else if (caught instanceof ApiError && caught.status === 507 && caught.detail === 'ota_insufficient_space') {
        setError('Недостаточно свободного места на хосте для обновления. Пакет сохранён; освободите место и повторите запуск.')
      } else if (caught instanceof ApiError && caught.detail === 'ota_incompatible') {
        setError('Пакет несовместим с установленной версией Robopark. Обновление не запускалось.')
      } else if (caught instanceof ApiError && caught.status === 409 && caught.detail === 'terminal_active') {
        setError('Завершите активные терминальные сессии и повторите установку. Пакет сохранён.')
      } else if (caught instanceof ApiError && ['ota_version_mismatch', 'ota_downgrade_forbidden'].includes(caught.detail ?? '')) {
        setError('Версия OTA-пакета не подходит для этого хоста. Обновление не запускалось.')
      } else if (caught instanceof ApiError && caught.status >= 400 && caught.status < 500) {
        setError('Сервер отклонил запуск обновления. Проверьте пакет и права доступа.')
      } else setError('Не удалось подтвердить запуск. Проверьте пароль и одноразовый код.')
      setStage('uploaded')
    } finally { onPostingChange(null) }
  }

  return <section aria-label="OTA-обновление" className="rp-ota">
    <Panel density="dense" title="Обновление системы" hint="Пакет сначала проверяется на этом устройстве, затем передаётся частями и повторно проверяется сервером.">
      {(!fresh || !capability?.available) && <Alert tone="warning">OTA сейчас недоступно на хосте. Выбор и проверка пакета доступны, установка заблокирована.</Alert>}
      {error && <Alert tone="error">{error}</Alert>}
      <div className="rp-ota-drop" onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); void choose(event.dataTransfer.files[0] ?? null) }}>
        <div className="rp-ota-drop__content">
          <span>Файл .ota, до 2 ГБ. Проверка выполняется локально.</span>
          <FileField accept=".ota,application/zip" disabled={active || stage === 'uploading' || stage === 'removing' || stage === 'starting'} label="Выберите OTA-пакет" onChange={event => void choose(event.target.files?.[0] ?? null)} selectedFileLabel={file?.name} />
        </div>
      </div>
      {(stage === 'checking' || stage === 'uploading') && <div className="rp-ota-progress"><span>{stage === 'checking' ? 'Проверка SHA‑256' : 'Передача на сервер'} · {Math.round(progress)}%</span><progress max="100" value={progress} /></div>}
      {manifest && sha256 && <div className="rp-ota-preview">
        <div><span>Версия</span><strong>{manifest.app_version}</strong></div><div><span>Размер</span><strong>{file ? bytes(file.size) : '—'}</strong></div>
        <div><span>Требуется свободного места</span><strong>{bytes(manifest.required_free_bytes)}</strong></div><div><span>SHA‑256</span><code>{sha256}</code></div>
        {manifest.compatible_from.length > 0 && <div className="rp-ota-wide"><span>Совместимо с</span><strong>{manifest.compatible_from.join(', ')}</strong></div>}
        {manifest.changes.length > 0 && <details className="rp-ota-wide"><summary>Что изменится</summary><ul>{manifest.changes.map((change, index) => <li key={`${index}:${change}`}>{change}</li>)}</ul></details>}
      </div>}
      {stage === 'ready' && <Button disabled={!fresh || !capability?.available || Boolean(currentVersion && !manifest?.compatible_from.includes(currentVersion))} onClick={() => void upload()} type="button">Проверить на сервере</Button>}
      {stage === 'removing' && <p role="status">Удаляем загруженный пакет…</p>}
      <Button disabled={Boolean(busy || listing)} onClick={() => void listUploads()} type="button">{listing ? 'Получаем список…' : savedUploads === null ? 'Показать мои загрузки' : 'Обновить список загрузок'}</Button>
      {savedUploads !== null && <section aria-label="Мои загрузки OTA">
        <p>Здесь только ваши файлы, ещё не используемые системной операцией. Удалённый файл можно загрузить заново.</p>
        {savedUploads.length === 0 ? <p>Нет загрузок, доступных для удаления.</p> : <ul className="rp-ota-upload-list">{savedUploads.map(row => <li key={row.upload_id}>
          <strong>{row.filename}</strong><span>{row.version ? `Версия ${row.version}` : 'Передача не завершена'} · {bytes(row.size)}</span>
          <code>{row.sha256}</code>
          <Button disabled={Boolean(busy || listing)} onClick={() => void removeUpload(row.upload_id)} type="button">Удалить {row.filename} ({row.sha256.slice(0, 8)})</Button>
        </li>)}</ul>}
      </section>}
      {stage === 'uploaded' && !active && <div className="rp-ota-confirm">
        <p>Загруженный файл можно удалить с сервера и загрузить заново. Установленная версия и данные сохраняются.</p>
        <Button disabled={listing} onClick={() => void removeUpload(uploadId)} type="button">Удалить загруженный пакет</Button>
        <label className="field"><span className="field-label">Введите {CONFIRMATION}</span><input autoComplete="off" onChange={event => setConfirmation(event.target.value)} value={confirmation} /></label>
        <label className="field"><span className="field-label">Пароль</span><input autoComplete="current-password" onChange={event => setPassword(event.target.value)} type="password" value={password} /></label>
        <label className="field"><span className="field-label">Код TOTP или восстановления</span><input autoComplete="one-time-code" onChange={event => setCode(event.target.value)} value={code} /></label>
        <Button disabled={!fresh || confirmation !== CONFIRMATION || !password || !code || Boolean(currentVersion && !manifest?.compatible_from.includes(currentVersion))} onClick={() => void install()} type="button">Установить {manifest?.app_version}</Button>
      </div>}
      {job?.kind === 'ota-update' && <div className="rp-ota-job">
        <StatusBadge tone={job.state === 'failed' ? 'critical' : job.state === 'succeeded' ? 'success' : 'info'}>{job.phase || job.state}</StatusBadge>
        <progress aria-label="Прогресс OTA-обновления" max="100" value={job.progress_percent ?? (['succeeded', 'failed'].includes(job.state) ? 100 : 0)} />
        {job.error && <Alert tone="error">{job.error === 'ota_insufficient_space'
          ? 'Недостаточно свободного места на хосте для обновления. Снимок данных не создавался; проверьте хранилище перед повтором.'
          : job.error === 'ota_rolled_back'
            ? 'Обновление не прошло проверку. Откат выполнен; проверьте состояние системы перед повторной попыткой.'
            : job.error === 'ota_rollback_failed'
              ? 'Обновление и откат завершились ошибкой: требуется ручное восстановление хоста. Не запускайте повторное обновление до проверки состояния.'
          : `Обновление завершилось ошибкой: ${job.error}. Проверьте состояние системы перед повторной попыткой.`}</Alert>}
      </div>}
    </Panel>
  </section>
}
