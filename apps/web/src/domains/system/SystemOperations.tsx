import { useMemo, useRef, useState } from 'react'
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

function payloadFor(kind: HostOperationKind, operationId: string, capabilityRevision: string): HostOperationPayload {
  const common = { operation_id: operationId, kind, capability_revision: capabilityRevision }
  if (kind === 'package-inspect') return { ...common, package: 'openssl' }
  if (kind === 'cleanup-preview') return { ...common, categories: ['diagnostics', 'logs', 'backups', 'releases'] }
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
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const initialFocus = useRef<HTMLInputElement>(null)
  const phrase = selected ? phrases[selected] : ''
  const fresh = capabilities.state === 'ready'
    && typeof capabilities.expires_at === 'string'
    && Date.parse(capabilities.expires_at) > Date.now()
  const revision = fresh ? capabilities.revision : null
  const valid = Boolean(selected && revision && confirmation === phrase && password && code)
  const active = job?.state === 'queued' || job?.state === 'running'
  const rows = useMemo(() => HOST_OPERATION_KINDS.map(kind => {
    const advertised = fresh && capabilities.operations[kind]?.available === true
    const runnable = advertised && directlyRunnable.has(kind)
    const reason = !advertised ? fresh ? 'Недоступно на этом хосте' : 'Снимок возможностей хоста недоступен или устарел'
      : !runnable ? kind === 'cleanup-execute' ? 'Сначала нужен поддерживаемый хостом план очистки' : 'Сначала выберите объект из результата безопасного обнаружения'
        : null
    return { kind, runnable, reason }
  }), [capabilities, fresh])

  const open = (kind: HostOperationKind) => {
    setSelected(kind); setConfirmation(''); setPassword(''); setCode(''); setError('')
  }
  const close = () => { if (!busy) setSelected(null) }
  const submit = async () => {
    if (!selected || !revision || !valid || busy || active || Date.parse(capabilities.expires_at ?? '') <= Date.now()) return
    const operationId = crypto.randomUUID()
    setBusy(true); setError('')
    try {
      const authorization = await client.reauthorize({
        password, code, operation_kind: selected, operation_id: operationId,
        capability_revision: revision,
      })
      const next = await client.startOperation(payloadFor(selected, operationId, revision), authorization.token)
      localStorage.setItem('robopark:system-operation', operationId)
      onAccepted(next)
      setSelected(null)
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
