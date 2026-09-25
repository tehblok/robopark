import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ApiError } from '../../api'
import { Alert, Panel } from '../../components/PageShell'
import { Button } from '../../design-system/actions/Button'
import { Dialog } from '../../design-system/overlays/Dialog'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { HOST_OPERATION_KINDS, type HostCapabilities, type HostOperationKind, type HostOperationPayload, type SystemClient, type SystemJob } from '../../opsApi'

const labels: Record<HostOperationKind, string> = {
  'release-update': 'Обновить Robopark', reinstall: 'Переустановить Robopark', rollback: 'Откатить версию',
  'package-inspect': 'Проверить пакет', 'package-update': 'Обновить пакет', 'service-restart': 'Перезапустить сервис',
  reboot: 'Перезагрузить хост', backup: 'Создать резервную копию', 'backup-verify': 'Проверить резервную копию',
  'backup-restore': 'Восстановить резервную копию', 'cleanup-preview': 'Предпросмотр очистки',
  'cleanup-execute': 'Выполнить очистку', diagnostics: 'Собрать диагностику', 'usb-discover': 'Найти USB',
  'usb-format': 'Форматировать USB', 'usb-select': 'Выбрать USB',
}
const phrases: Record<HostOperationKind, string> = Object.fromEntries(
  HOST_OPERATION_KINDS.map(kind => [kind, `ЗАПУСТИТЬ ${kind.toUpperCase()}`]),
) as Record<HostOperationKind, string>
const directlyRunnable = new Set<HostOperationKind>(['package-inspect', 'cleanup-preview', 'diagnostics', 'usb-discover'])
const UUID_PATTERN = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/

function payloadFor(kind: HostOperationKind, operationId: string, capabilityRevision: string, confirmation: string, deviceUuid: string, backupId: string): HostOperationPayload {
  const common = { operation_id: operationId, kind, capability_revision: capabilityRevision, confirmation }
  if (kind === 'package-inspect') return { ...common, package: 'openssl' }
  if (kind === 'cleanup-preview') return { ...common, categories: ['diagnostics', 'logs', 'backups', 'releases'] }
  if (kind === 'usb-select') return { ...common, device_uuid: deviceUuid }
  if (kind === 'backup-verify') return { ...common, backup_id: backupId }
  return common
}

export function SystemOperations({ client, capabilities, job, onAccepted, onRefreshCapabilities }: {
  client: SystemClient; capabilities: HostCapabilities; job: SystemJob | null
  onAccepted: (job: SystemJob) => void; onRefreshCapabilities: () => Promise<void>
}) {
  const [selected, setSelected] = useState<HostOperationKind | null>(null)
  const [confirmation, setConfirmation] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [deviceUuid, setDeviceUuid] = useState('')
  const [backupId, setBackupId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [fresh, setFresh] = useState(capabilities.state === 'ready' && typeof capabilities.expires_at === 'string')
  const [reservedOperationId, setReservedOperationId] = useState(() => localStorage.getItem('robopark:system-operation'))
  const initialFocus = useRef<HTMLInputElement>(null)
  const phrase = selected ? phrases[selected] : ''
  const revision = fresh ? capabilities.revision : null
  const valid = Boolean(selected && revision && confirmation === phrase && password && code
    && (selected !== 'usb-select' || deviceUuid)
    && (selected !== 'backup-verify' || UUID_PATTERN.test(backupId)))
  const active = Boolean(reservedOperationId) || job?.state === 'queued' || job?.state === 'running'
  const devices = job?.host_result?.devices?.filter(device => device.removable) ?? []
  const rows = HOST_OPERATION_KINDS.map(kind => {
    const advertised = fresh && capabilities.operations[kind]?.available === true
    const runnable = advertised && (directlyRunnable.has(kind)
      || kind === 'usb-select' && devices.some(device => device.device_uuid === deviceUuid)
      || kind === 'backup-verify' && UUID_PATTERN.test(backupId))
    const unavailableReason = capabilities.operations[kind]?.unavailable_reason
    const reason = !advertised ? !fresh ? 'Снимок возможностей хоста недоступен или устарел'
      : unavailableReason === 'context_unavailable' ? 'Безопасный контекст для этой операции на хосте не настроен'
      : 'Недоступно на этом хосте'
      : !runnable ? kind === 'cleanup-execute' ? 'Сначала нужен поддерживаемый хостом план очистки'
        : kind === 'backup-verify' ? 'Укажите точный UUID резервной копии; внешний ключ должен быть безопасно установлен на хосте'
        : 'Сначала выберите объект из результата безопасного обнаружения'
        : null
    return { kind, runnable, reason }
  })

  const open = (kind: HostOperationKind) => {
    setSelected(kind); setConfirmation(''); setPassword(''); setCode(''); setError('')
  }
  const close = () => { if (!busy) setSelected(null) }

  useLayoutEffect(() => {
    let timer: number | undefined
    const expiresAt = typeof capabilities.expires_at === 'string' ? Date.parse(capabilities.expires_at) : Number.NaN
    const update = () => {
      const remaining = expiresAt - Date.now()
      const current = capabilities.state === 'ready' && Number.isFinite(expiresAt) && remaining > 0
      setFresh(current)
      if (current) timer = window.setTimeout(update, Math.min(remaining, 2_147_000_000))
    }
    update()
    return () => window.clearTimeout(timer)
  }, [capabilities.expires_at, capabilities.state])

  useEffect(() => {
    if (job && (job.state === 'succeeded' || job.state === 'failed')) setReservedOperationId(null)
  }, [job])

  const submit = async () => {
    if (!selected || !revision || !valid || busy || active || Date.parse(capabilities.expires_at ?? '') <= Date.now()) return
    const operationId = crypto.randomUUID()
    const operationKind = selected
    setBusy(true); setError('')
    try {
      const authorization = await client.reauthorize({
        password, code, operation_kind: operationKind, operation_id: operationId,
        capability_revision: revision,
      })
      try {
        localStorage.setItem('robopark:system-operation', operationId)
        setReservedOperationId(operationId)
      } catch {
        setError('Не удалось безопасно сохранить идентификатор операции. Запуск отменён.')
        return
      }
      try {
        const next = await client.startOperation(payloadFor(operationKind, operationId, revision, confirmation, deviceUuid, backupId), authorization.token)
        onAccepted(next)
        setSelected(null)
      } catch {
        onAccepted({ id: operationId, kind: operationKind, state: 'queued', phase: 'Проверяем получение запроса', progress_percent: 0, error: null })
        setSelected(null)
      }
    } catch (caught) {
      if (caught instanceof ApiError && ['capabilities_changed', 'capability_unavailable', 'capabilities_unavailable'].includes(caught.detail ?? '')) {
        setError('Возможности хоста изменились. Список обновлён; подтвердите операцию заново.')
        setConfirmation(''); setPassword(''); setCode('')
        await onRefreshCapabilities()
      } else setError('Операция не запущена. Проверьте пароль и одноразовый код.')
    } finally { setBusy(false) }
  }

  return <section aria-label="Управляемые операции" className="rp-system-operations">
    <Panel title="Управляемые операции" hint="Доступность поступает с хоста. Произвольные команды и аргументы не принимаются.">
      {capabilities.state !== 'ready' && <Alert tone="warning">Свежий список возможностей хоста недоступен. Все операции заблокированы.</Alert>}
      {devices.length > 0 && <label className="field"><span className="field-label">Обнаруженное USB-устройство</span><select onChange={event => setDeviceUuid(event.target.value)} value={deviceUuid}><option value="">Выберите устройство</option>{devices.map(device => <option key={device.device_uuid} value={device.device_uuid}>{device.device_uuid}{device.mounted ? ' · подключено' : ''}</option>)}</select></label>}
      {fresh && capabilities.operations['backup-verify']?.available && <label className="field"><span className="field-label">UUID резервной копии</span><input autoComplete="off" onChange={event => setBackupId(event.target.value.trim())} value={backupId} /></label>}
      <div className="rp-system-operation-list">{rows.map(({ kind, runnable, reason }) => <div className="rp-system-operation" key={kind}>
        <div><strong>{labels[kind]}</strong>{reason && <p>{reason}</p>}</div>
        <Button disabled={!runnable || active} onClick={() => open(kind)} size="compact" type="button">{labels[kind]}</Button>
      </div>)}</div>
    </Panel>
    {job?.id && <Panel title="Текущая операция">
      <p className="rp-system-operation-id">{job.id}</p>
      <div className="rp-system-progress"><StatusBadge tone={job.state === 'failed' ? 'critical' : job.state === 'succeeded' ? 'success' : 'info'}>{job.phase || job.state}</StatusBadge><progress aria-label="Прогресс операции" max="100" value={job.progress_percent ?? (job.state === 'succeeded' || job.state === 'failed' ? 100 : 0)} /></div>
      {job.error && <Alert tone="error">Операция завершилась ошибкой: {job.error}</Alert>}
    </Panel>}
    <Dialog description="Введите фразу без изменений и заново подтвердите личность." dismissible={!busy} initialFocusRef={initialFocus} onOpenChange={open => { if (!open) close() }} open={selected !== null} title="Подтвердить операцию">
      {error && <Alert tone="error">{error}</Alert>}
      <label className="field"><span className="field-label">Введите {phrase}</span><input autoComplete="off" disabled={busy} onChange={event => setConfirmation(event.target.value)} ref={initialFocus} value={confirmation} /></label>
      <label className="field"><span className="field-label">Пароль</span><input autoComplete="current-password" disabled={busy} onChange={event => setPassword(event.target.value)} type="password" value={password} /></label>
      <label className="field"><span className="field-label">Код TOTP или восстановления</span><input autoComplete="one-time-code" disabled={busy} onChange={event => setCode(event.target.value)} value={code} /></label>
      <div className="form-actions"><Button busy={busy} disabled={!valid || active} onClick={() => void submit()} type="button">Запустить</Button></div>
    </Dialog>
  </section>
}
