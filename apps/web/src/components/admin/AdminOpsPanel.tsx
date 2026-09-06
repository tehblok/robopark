import { type FormEvent, useContext, useEffect, useState } from 'react'
import { api, ApiError, type OpsJob } from '../../api'
import { mapApiError } from '../../i18n/errors'
import { ru } from '../../i18n/ru'
import { Alert, Panel } from '../PageShell'
import { AuthContext } from '../../auth-context'
import { resourceStore, useCachedResource } from '../../lib/resource'
import { adminResourceKey } from './adminResources'

function isActive(job: OpsJob | null) {
  return job?.state === 'queued' || job?.state === 'running'
}

function isIdle(job: OpsJob | null) {
  return !job || job.state === 'idle' || job.id === ''
}

async function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

export function AdminOpsPanel() {
  const [job, setJob] = useState<OpsJob | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [restoreFile, setRestoreFile] = useState<File | null>(null)
  const [restoreConfirm, setRestoreConfirm] = useState('')
  const [updateFile, setUpdateFile] = useState<File | null>(null)
  const [updateConfirm, setUpdateConfirm] = useState('')

  const auth = useContext(AuthContext)
  const jobResource = useCachedResource(adminResourceKey('ops-job', auth?.user ?? null), async () => {
    try { return await api.opsJob() }
    catch (failure) {
      if (failure instanceof ApiError && failure.status === 404) return null
      throw failure
    }
  }, { persist: false, refreshIntervalMs: isActive(job) ? 1000 : 30_000, staleTimeMs: isActive(job) ? 1000 : 30_000, trackProgress: false })
  useEffect(() => { if (jobResource.data !== undefined) setJob(jobResource.data) }, [jobResource.data])
  const loadError = jobResource.error ? mapApiError(jobResource.error, ru.errors.load) : ''

  const run = async (action: () => Promise<OpsJob>) => {
    setError('')
    setBusy(true)
    try {
      const next = await action()
      setJob(next)
      resourceStore.invalidate(adminResourceKey('ops-job', auth?.user ?? null))
      void jobResource.refresh()
    } catch (caught) {
      setError(mapApiError(caught, ru.errors.generic))
    } finally {
      setBusy(false)
    }
  }

  const startSnapshot = () => run(() => api.opsSnapshot())

  const downloadSnapshot = async () => {
    setError('')
    try {
      const blob = await api.opsArtifact()
      await downloadBlob(blob, job?.artifact_ready ? `robopark-snapshot-${job.id}.zip` : 'robopark-snapshot.zip')
    } catch (caught) {
      setError(mapApiError(caught, ru.errors.generic))
    }
  }

  const restore = async (event: FormEvent) => {
    event.preventDefault()
    if (!restoreFile) return
    await run(() => api.opsRestore(restoreFile, restoreConfirm))
  }

  const update = async (event: FormEvent) => {
    event.preventDefault()
    if (!updateFile) return
    await run(() => api.opsUpdate(updateFile, updateConfirm))
  }

  const restorePhrase = job?.restore_phrase ?? 'ВОССТАНОВИТЬ'
  const updatePhrase = job?.update_phrase ?? 'ОБНОВИТЬ'

  return (
    <div className="ops-stack">
      {(error || loadError) && <Alert tone="error">{error || loadError}</Alert>}
      {job && !isIdle(job) && (
        <Panel hint={isActive(job) ? job.phase : undefined} title="Текущая операция">
          <div className="stat-grid">
            <div className="stat">
              <span className="stat-label">Тип</span>
              <span className="stat-value">{job.kind}</span>
            </div>
            <div className="stat">
              <span className="stat-label">Статус</span>
              <span className="stat-value">{job.state}</span>
            </div>
          </div>
          {job.log ? <pre className="ops-log">{job.log}</pre> : null}
          {job.error ? (
            <Alert tone="error">{mapApiError(new ApiError(400, job.error), ru.errors.generic)}</Alert>
          ) : null}
          {job.restart_required && job.state === 'succeeded' ? (
            <Alert tone="warning">{ru.ops.restartHint}</Alert>
          ) : null}
          {isActive(job) ? (
            <div className="form-actions">
              <button
                className="btn btn-secondary"
                disabled={busy}
                onClick={() => void run(() => api.opsAbort())}
                type="button"
              >
                Прервать и снять техработы
              </button>
            </div>
          ) : null}
        </Panel>
      )}
      {isIdle(job) && <p className="muted">{ru.ops.jobIdle}</p>}

      <Panel hint={ru.ops.snapshotHint} title={ru.ops.snapshotTitle}>
        <p className="muted">{ru.ops.secretKeyHint}</p>
        <div className="form-actions">
          <button className="btn" disabled={busy || isActive(job)} onClick={() => void startSnapshot()} type="button">
            {ru.ops.snapshotStart}
          </button>
          <button
            className="btn btn-secondary"
            disabled={busy || !job?.artifact_ready}
            onClick={() => void downloadSnapshot()}
            type="button"
          >
            {ru.ops.snapshotDownload}
          </button>
        </div>
      </Panel>

      <Panel hint={ru.ops.restoreHint} title={ru.ops.restoreTitle}>
        <form className="form-grid" onSubmit={(event) => void restore(event)}>
          <label className="field">
            <span className="field-label">{ru.ops.chooseZip}</span>
            <input
              accept=".zip,application/zip"
              onChange={(event) => setRestoreFile(event.target.files?.[0] ?? null)}
              type="file"
            />
          </label>
          <label className="field">
            <span className="field-label">
              {ru.ops.restoreConfirmLabel}: {restorePhrase}
            </span>
            <input
              autoComplete="off"
              onChange={(event) => setRestoreConfirm(event.target.value)}
              value={restoreConfirm}
            />
          </label>
          <div className="form-actions">
            <button className="btn btn-danger" disabled={busy || isActive(job) || !restoreFile} type="submit">
              {ru.ops.restoreSubmit}
            </button>
          </div>
        </form>
      </Panel>

      <Panel hint={ru.ops.updateHint} title={ru.ops.updateTitle}>
        <form className="form-grid" onSubmit={(event) => void update(event)}>
          <label className="field">
            <span className="field-label">{ru.ops.chooseZip}</span>
            <input
              accept=".zip,application/zip"
              onChange={(event) => setUpdateFile(event.target.files?.[0] ?? null)}
              type="file"
            />
          </label>
          <label className="field">
            <span className="field-label">
              {ru.ops.updateConfirmLabel}: {updatePhrase}
            </span>
            <input
              autoComplete="off"
              onChange={(event) => setUpdateConfirm(event.target.value)}
              value={updateConfirm}
            />
          </label>
          <div className="form-actions">
            <button className="btn" disabled={busy || isActive(job) || !updateFile} type="submit">
              {ru.ops.updateSubmit}
            </button>
          </div>
        </form>
      </Panel>
    </div>
  )
}
