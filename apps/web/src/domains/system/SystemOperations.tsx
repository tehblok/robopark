import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ApiError } from '../../api'
import { Alert, Panel } from '../../components/PageShell'
import { Button } from '../../design-system/actions/Button'
import { Dialog } from '../../design-system/overlays/Dialog'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { HOST_OPERATION_KINDS, type HostCapabilities, type HostOperationContext, type HostOperationKind, type HostOperationPayload, type SystemClient, type SystemJob } from '../../opsApi'
import { clearOperationReservation, readOperationReservation, safeOperationDraft, writeOperationReservation, type OperationActor } from './operationReservation'

const labels: Record<HostOperationKind, string> = {
  'ota-update': 'Установить OTA-пакет',
  rollback: 'Откатить версию',
  'package-inspect': 'Проверить пакет', 'package-update': 'Обновить пакет', 'service-restart': 'Перезапустить сервис',
  reboot: 'Перезагрузить хост', backup: 'Создать резервную копию', 'backup-verify': 'Проверить резервную копию',
  'backup-restore': 'Восстановить резервную копию', 'cleanup-preview': 'Предпросмотр очистки',
  'cleanup-execute': 'Выполнить очистку', 'docker-image-preview': 'Просмотреть образы Docker',
  'docker-image-execute': 'Удалить показанные образы',
  'builder-cache-preview': 'Просмотреть кэш BuildKit',
  'builder-cache-execute': 'Очистить показанную запись BuildKit',
  diagnostics: 'Собрать диагностику', 'usb-discover': 'Найти USB',
  'usb-format': 'Форматировать USB', 'usb-select': 'Выбрать USB',
}
const fixedPhrases: Record<HostOperationKind, string> = Object.fromEntries(
  HOST_OPERATION_KINDS.map(kind => [kind, `ЗАПУСТИТЬ ${kind.toUpperCase()}`]),
) as Record<HostOperationKind, string>
fixedPhrases.rollback = 'ROLLBACK ROBOPARK'
fixedPhrases.reboot = 'REBOOT ROBOPARK'
fixedPhrases.backup = 'BACKUP ROBOPARK'
fixedPhrases['backup-restore'] = 'RESTORE ROBOPARK BACKUP'
fixedPhrases['cleanup-execute'] = 'CLEAN ROBOPARK'
fixedPhrases['docker-image-execute'] = 'CLEAN ROBOPARK IMAGES'
fixedPhrases['builder-cache-execute'] = 'CLEAN ROBOPARK BUILD CACHE'
const directlyRunnable = new Set<HostOperationKind>(['package-inspect', 'cleanup-preview', 'docker-image-preview', 'builder-cache-preview', 'diagnostics', 'usb-discover'])
const UUID_PATTERN = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/
const cleanupRoots = {
  diagnostics: '/var/lib/robopark/diagnostics/',
  logs: '/var/log/robopark/',
  backups: '/var/lib/robopark/backups/',
  releases: '/opt/robopark/releases/',
  ota_cache: '/var/lib/robopark/ops/state/ota-packages/',
} as const
type CleanupCategory = keyof typeof cleanupRoots
const cleanupOptions: { category: CleanupCategory; label: string }[] = [
  { category: 'diagnostics', label: 'Диагностика' },
  { category: 'logs', label: 'Журналы Robopark' },
  { category: 'ota_cache', label: 'Кэш завершённых OTA' },
  { category: 'backups', label: 'Резервные копии' },
  { category: 'releases', label: 'Релизы' },
]

function confirmationFor(kind: HostOperationKind | null, packageName: string, serviceName: string, deviceUuid: string): string {
  if (!kind) return ''
  if (kind === 'package-update') return `UPDATE PACKAGE ${packageName}`
  if (kind === 'service-restart') return `RESTART SERVICE ${serviceName}`
  if (kind === 'usb-format') return `FORMAT USB ${deviceUuid}`
  return fixedPhrases[kind]
}

function payloadFor(kind: HostOperationKind, operationId: string, capabilityRevision: string, confirmation: string, packageName: string, serviceName: string, release: string, deviceUuid: string, backupId: string, planId: string, imagePlanId: string, builderPlanId: string, cleanupCategories: CleanupCategory[]): HostOperationPayload {
  const common = { operation_id: operationId, kind, capability_revision: capabilityRevision, confirmation }
  if (kind === 'rollback') return { ...common, release }
  if (kind === 'package-inspect' || kind === 'package-update') return { ...common, package: packageName }
  if (kind === 'service-restart') return { ...common, service: serviceName }
  if (kind === 'backup') return { ...common, device_uuid: deviceUuid }
  if (kind === 'cleanup-preview') return { ...common, categories: cleanupCategories }
  if (kind === 'cleanup-execute') return { ...common, plan_id: planId }
  if (kind === 'docker-image-execute') return { ...common, plan_id: imagePlanId }
  if (kind === 'builder-cache-execute') return { ...common, plan_id: builderPlanId }
  if (kind === 'usb-select') return { ...common, device_uuid: deviceUuid }
  if (kind === 'usb-format') return { ...common, device_uuid: deviceUuid, confirmation_repeat: confirmation }
  if (kind === 'backup-verify' || kind === 'backup-restore') return { ...common, backup_id: backupId }
  return common
}

function operationResult(job: SystemJob, client: SystemClient, includeMaintenance = false) {
  const result = job.host_result
  const packageResult = result?.package_result
  const backupResult = result?.backup_result
  const actionResult = result?.action_result
  const usbResult = result?.usb_result
  const diagnosticReady = job.kind === 'diagnostics' && job.state === 'succeeded' && (job.artifact_ready || result?.diagnostics_ready)
  return <div className="rp-system-result">
    {packageResult && <p>{packageResult.package}{packageResult.version ? ` · версия ${packageResult.version}` : packageResult.installed === false ? ' · не установлен' : ' · версия не определена'}{packageResult.updated ? ' · обновлён' : ''}</p>}
    {backupResult && <p>Резервная копия <code>{backupResult.backup_id}</code>{backupResult.restored ? ' восстановлена' : backupResult.verified ? ' проверена' : ' создана'}.</p>}
    {actionResult?.rolled_back && <p>Выполнен откат на версию <strong>{actionResult.release}</strong>.</p>}
    {actionResult?.restarted && <p>Сервис <code>{actionResult.service}</code> перезапущен.</p>}
    {actionResult?.reboot_scheduled && <p>Перезагрузка хоста запланирована.</p>}
    {usbResult?.selected && <p>USB <code>{usbResult.device_uuid}</code> выбран для резервных копий.</p>}
    {usbResult?.formatted && <p>USB <code>{usbResult.device_uuid}</code> отформатирован.</p>}
    {result?.devices && result.devices.length > 0 && <p>Найдено съёмных USB-устройств: {result.devices.filter(device => device.removable).length}.</p>}
    {includeMaintenance && result?.cleanup_preview && <div className="rp-system-cleanup-preview"><p>Предпросмотр очистки: {result.cleanup_preview.planned.length} целей, {new Intl.NumberFormat('ru-RU').format(result.cleanup_preview.total_bytes)} байт.</p><ul>{result.cleanup_preview.planned.map(item => <li key={`${item.category}:${item.path}`}><code>{cleanupRoots[item.category]}{item.path}</code><span>{new Intl.NumberFormat('ru-RU').format(item.bytes)} байт</span></li>)}</ul></div>}
    {includeMaintenance && result?.cleanup_result && <div className="rp-system-cleanup-preview"><p>Подтверждённо удалено файлов: {result.cleanup_result.deleted_count}.</p><ul>{result.cleanup_result.deleted.map(item => <li key={`${item.category}:${item.path}`}><code>{cleanupRoots[item.category]}{item.path}</code><span>{new Intl.NumberFormat('ru-RU').format(item.bytes)} байт</span></li>)}</ul></div>}
    {includeMaintenance && result?.docker_image_preview && <div className="rp-system-cleanup-preview"><p>Предпросмотр Docker: {result.docker_image_preview.planned.length} образов, {new Intl.NumberFormat('ru-RU').format(result.docker_image_preview.total_reported_bytes)} байт.</p><ul>{result.docker_image_preview.planned.map(item => <li key={item.tag}><code>{item.tag}</code><span>{new Intl.NumberFormat('ru-RU').format(item.reported_bytes)} байт</span></li>)}</ul></div>}
    {includeMaintenance && result?.docker_image_result && <div className="rp-system-cleanup-preview"><p>Подтверждённо удалено меток образов: {result.docker_image_result.deleted_count}.</p><ul>{result.docker_image_result.deleted.map(item => <li key={item.tag}><code>{item.tag}</code><span>{new Intl.NumberFormat('ru-RU').format(item.reported_bytes)} байт</span></li>)}</ul></div>}
    {includeMaintenance && result?.builder_cache_preview && <div className="rp-system-cleanup-preview"><p>Предпросмотр BuildKit: {result.builder_cache_preview.planned.length} запись, {new Intl.NumberFormat('ru-RU').format(result.builder_cache_preview.total_reported_bytes)} байт.</p><ul>{result.builder_cache_preview.planned.map(item => <li key={item.id}><code>{item.id}</code><span>{new Intl.NumberFormat('ru-RU').format(item.reported_bytes)} байт</span></li>)}</ul></div>}
    {includeMaintenance && result?.builder_cache_result && <div className="rp-system-cleanup-preview"><p>Подтверждённо удалено записей BuildKit: {result.builder_cache_result.deleted_count}.</p><ul>{result.builder_cache_result.deleted.map(item => <li key={item.id}><code>{item.id}</code><span>{new Intl.NumberFormat('ru-RU').format(item.reported_bytes)} байт</span></li>)}</ul></div>}
    {diagnosticReady && <a className="rp-button rp-button--primary rp-button--compact" download href={client.operationArtifactUrl(job.id)}>Скачать диагностику</a>}
    {job.kind === 'diagnostics' && job.state === 'succeeded' && !diagnosticReady && <Alert tone="warning">Архив не создан. Соберите диагностику заново.</Alert>}
    {job.state === 'succeeded' && !result && <p>Хост подтвердил выполнение операции.</p>}
  </div>
}

export function SystemOperations({ actor, client, capabilities, context, history, job, onAccepted, onPostingChange, onRefreshCapabilities }: {
  actor: OperationActor
  client: SystemClient; capabilities: HostCapabilities; context: HostOperationContext | null; history: SystemJob[]; job: SystemJob | null
  onAccepted: (job: SystemJob) => void; onPostingChange: (operationId: string | null) => void
  onRefreshCapabilities: () => Promise<void>
}) {
  const [selected, setSelected] = useState<HostOperationKind | null>(null)
  const [confirmation, setConfirmation] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [deviceUuid, setDeviceUuid] = useState(context?.selected_device_uuid ?? '')
  const [backupId, setBackupId] = useState(context?.backups[0]?.backup_id ?? '')
  const [packageName, setPackageName] = useState(context?.packages[0] ?? '')
  const [serviceName, setServiceName] = useState(context?.services[0] ?? '')
  const [cleanupCategories, setCleanupCategories] = useState<CleanupCategory[]>(['diagnostics', 'logs', 'ota_cache'])
  const [busy, setBusy] = useState(false)
  const [retrying, setRetrying] = useState(false)
  const [error, setError] = useState('')
  const [fresh, setFresh] = useState(capabilities.state === 'ready' && typeof capabilities.expires_at === 'string')
  const [contextUsable, setContextUsable] = useState(() => Boolean(context && Date.parse(context.expires_at) > Date.now()))
  const [reservedOperationId, setReservedOperationId] = useState(() => readOperationReservation(actor)?.id ?? null)
  const initialFocus = useRef<HTMLInputElement>(null)
  const phrase = confirmationFor(selected, packageName, serviceName, deviceUuid)
  const revision = fresh ? capabilities.revision : null
  const contextFresh = contextUsable
  const safeContext = contextFresh ? context : null
  const reservation = readOperationReservation(actor)
  const retryDraft = retrying ? reservation?.draft : undefined
  const needsRetry = job?.receipt_state === 'received' || job?.phase.startsWith('Запрос не подтверждён')
  const active = (Boolean(reservedOperationId) || job?.state === 'queued' || job?.state === 'running') && !retrying
  const resultJobs = [job, ...history].filter((item, index, items): item is SystemJob => Boolean(item) && items.findIndex(other => other?.id === item?.id) === index)
  const devices = [...(safeContext?.devices ?? []), ...(job?.host_result?.devices ?? [])].filter((device, index, items) => device.removable && items.findIndex(other => other.device_uuid === device.device_uuid) === index)
  const backups = safeContext?.backups ?? []
  const cleanupPreview = job?.kind === 'cleanup-preview' && job.state === 'succeeded' ? job.host_result?.cleanup_preview : null
  const cleanupResult = job?.kind === 'cleanup-execute' ? job.host_result?.cleanup_result : null
  const dockerImagePreview = job?.kind === 'docker-image-preview' && job.state === 'succeeded' ? job.host_result?.docker_image_preview : null
  const dockerImageResult = job?.kind === 'docker-image-execute' ? job.host_result?.docker_image_result : null
  const builderCachePreview = job?.kind === 'builder-cache-preview' && job.state === 'succeeded' ? job.host_result?.builder_cache_preview : null
  const builderCacheResult = job?.kind === 'builder-cache-execute' ? job.host_result?.builder_cache_result : null
  const executablePlanId = cleanupPreview && !cleanupPreview.blocked && cleanupPreview.planned.length > 0
    && UUID_PATTERN.test(cleanupPreview.plan_id) ? cleanupPreview.plan_id : ''
  const imagePlanId = dockerImagePreview && !dockerImagePreview.blocked && dockerImagePreview.planned.length > 0
    && dockerImagePreview.plan_id && UUID_PATTERN.test(dockerImagePreview.plan_id) ? dockerImagePreview.plan_id : ''
  const builderPlanId = builderCachePreview && !builderCachePreview.blocked && builderCachePreview.planned.length === 1
    && builderCachePreview.plan_id && UUID_PATTERN.test(builderCachePreview.plan_id) ? builderCachePreview.plan_id : ''
  const valid = Boolean(selected && revision && confirmation === phrase && password && code
    && (selected !== 'rollback' || Boolean(safeContext?.rollback_release))
    && (!['package-inspect', 'package-update'].includes(selected) || safeContext?.packages.includes(packageName))
    && (selected !== 'service-restart' || safeContext?.services.includes(serviceName))
    && (!['backup', 'usb-select', 'usb-format'].includes(selected) || devices.some(device => device.device_uuid === deviceUuid))
    && (selected !== 'usb-format' || devices.some(device => device.device_uuid === deviceUuid && !device.mounted))
    && (selected !== 'cleanup-preview' || Boolean(retrying || cleanupCategories.length))
    && (selected !== 'docker-image-execute' || Boolean(retrying || imagePlanId))
    && (selected !== 'builder-cache-execute' || Boolean(retrying || builderPlanId))
    && (!['backup-verify', 'backup-restore'].includes(selected) || backups.some(backup => backup.backup_id === backupId)))
  const rows = HOST_OPERATION_KINDS.filter(kind => kind !== 'ota-update').map(kind => {
    const advertised = fresh && capabilities.operations[kind]?.available === true
    const runnable = advertised && (directlyRunnable.has(kind) && (kind !== 'cleanup-preview' || cleanupCategories.length > 0)
      && (kind !== 'package-inspect' || safeContext?.packages.includes(packageName))
      || kind === 'rollback' && Boolean(safeContext?.rollback_release)
      || (kind === 'package-update') && safeContext?.packages.includes(packageName)
      || kind === 'service-restart' && safeContext?.services.includes(serviceName)
      || kind === 'reboot'
      || kind === 'backup' && devices.some(device => device.device_uuid === deviceUuid)
      || kind === 'cleanup-execute' && Boolean(executablePlanId)
      || kind === 'docker-image-execute' && Boolean(imagePlanId)
      || kind === 'builder-cache-execute' && Boolean(builderPlanId)
      || kind === 'usb-select' && devices.some(device => device.device_uuid === deviceUuid)
      || kind === 'usb-format' && devices.some(device => device.device_uuid === deviceUuid && !device.mounted)
      || (kind === 'backup-verify' || kind === 'backup-restore') && backups.some(backup => backup.backup_id === backupId))
    const unavailableReason = capabilities.operations[kind]?.unavailable_reason
    const reason = !advertised ? !fresh ? 'Снимок возможностей хоста недоступен или устарел'
      : unavailableReason === 'context_unavailable' ? 'Безопасный контекст для этой операции на хосте не настроен'
      : 'Недоступно на этом хосте'
      : !runnable ? kind === 'cleanup-execute' ? cleanupPreview?.blocked
        ? 'Предпросмотр заблокирован проверкой безопасности. Очистка недоступна.'
        : cleanupPreview && cleanupPreview.planned.length === 0
          ? 'Подходящих файлов для удаления нет.'
          : 'Нажмите «Предпросмотр очистки», чтобы увидеть список файлов и объём.'
        : kind === 'docker-image-execute' ? 'Сначала нужен точный предпросмотр образов Docker'
        : kind === 'builder-cache-execute' ? 'Сначала нужен точный предпросмотр кэша собственного builder Robopark'
        : kind === 'cleanup-preview' ? 'Выберите хотя бы одну категорию'
        : kind === 'rollback' ? 'Нет проверенного предыдущего релиза для отката'
        : kind === 'package-update' ? 'Выберите пакет из списка хоста'
        : kind === 'service-restart' ? 'Выберите сервис из списка хоста'
        : kind === 'backup-verify' || kind === 'backup-restore' ? 'Выберите резервную копию из списка хоста'
        : 'Сначала выберите объект из безопасного контекста хоста'
        : null
    return { kind, advertised, runnable, reason }
  })
  const availableRows = rows.filter(row => row.advertised)
  const unavailableRows = rows.filter(row => !row.advertised)
  const operationRows = (items: typeof rows) => items.map(({ kind, runnable, reason }) => <div className="rp-system-operation" key={kind}>
    <div><strong>{kind === 'docker-image-execute' ? 'Очистка образов Docker' : labels[kind]}</strong>{reason && <p>{reason}</p>}</div>
    <Button disabled={!runnable || active} onClick={() => open(kind)} size="compact" type="button">{labels[kind]}</Button>
  </div>)

  const open = (kind: HostOperationKind) => {
    setRetrying(false); setSelected(kind); setConfirmation(''); setPassword(''); setCode(''); setError('')
  }
  const openRetry = () => {
    const stored = readOperationReservation(actor)
    if (!stored?.draft || !stored.kind) return
    setRetrying(true); setSelected(stored.kind); setConfirmation(''); setPassword(''); setCode(''); setError('')
    setDeviceUuid(typeof stored.draft.device_uuid === 'string' ? stored.draft.device_uuid : '')
    setBackupId(typeof stored.draft.backup_id === 'string' ? stored.draft.backup_id : '')
    setPackageName(typeof stored.draft.package === 'string' ? stored.draft.package : packageName)
    setServiceName(typeof stored.draft.service === 'string' ? stored.draft.service : serviceName)
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

  useLayoutEffect(() => {
    let timer: number | undefined
    const expiresAt = context ? Date.parse(context.expires_at) : Number.NaN
    const update = () => {
      const remaining = expiresAt - Date.now()
      const usable = Boolean(context && Number.isFinite(expiresAt) && remaining > 0)
      setContextUsable(usable)
      if (usable) timer = window.setTimeout(update, Math.min(remaining, 2_147_000_000))
    }
    update()
    return () => window.clearTimeout(timer)
  }, [context])

  useEffect(() => {
    const packages = context?.packages ?? []
    const services = context?.services ?? []
    const backups = context?.backups ?? []
    const devices = (context?.devices ?? []).filter(device => device.removable)
    setPackageName(current => packages.includes(current) ? current : packages[0] ?? '')
    setServiceName(current => services.includes(current) ? current : services[0] ?? '')
    setBackupId(current => backups.some(backup => backup.backup_id === current) ? current : backups[0]?.backup_id ?? '')
    setDeviceUuid(current => {
      if (devices.some(device => device.device_uuid === current)) return current
      const selectedDevice = context?.selected_device_uuid ?? ''
      return devices.some(device => device.device_uuid === selectedDevice) ? selectedDevice : ''
    })
  }, [context])

  useEffect(() => {
    if (job && (job.receipt_state === 'terminal' || job.state === 'succeeded' || job.state === 'failed')) setReservedOperationId(null)
  }, [job])

  const submit = async () => {
    const operationRevision = retryDraft?.capability_revision ?? revision
    if (!selected || !operationRevision || !valid || busy || active || Date.parse(capabilities.expires_at ?? '') <= Date.now()) return
    const operationId = retryDraft?.operation_id ?? crypto.randomUUID()
    const operationKind = selected
    const reservedAt = Date.now()
    setBusy(true); setError('')
    const payload = retryDraft
      ? { ...retryDraft, confirmation: phrase, ...(operationKind === 'usb-format' ? { confirmation_repeat: phrase } : {}) } as HostOperationPayload
      : payloadFor(operationKind, operationId, operationRevision, confirmation, packageName, serviceName, safeContext?.rollback_release ?? '', deviceUuid, backupId, executablePlanId, imagePlanId, builderPlanId, cleanupCategories)
    try {
      writeOperationReservation(actor, { id: operationId, kind: operationKind, created_at: reservedAt, phase: 'posting', draft: safeOperationDraft(payload) })
      setReservedOperationId(operationId)
      onPostingChange(operationId)
    } catch {
      setError('Не удалось безопасно сохранить идентификатор операции. Запуск отменён.')
      setBusy(false)
      return
    }
    try {
      const authorization = await client.reauthorize({
        password, code, operation_kind: operationKind, operation_id: operationId,
        capability_revision: operationRevision,
      })
      try {
        const next = await client.startOperation(payload, authorization.token)
        writeOperationReservation(actor, { id: operationId, kind: operationKind, created_at: reservedAt, phase: 'reconciling', draft: safeOperationDraft(payload) })
        onAccepted(next)
        setSelected(null)
      } catch (caught) {
        if (caught instanceof ApiError && caught.status === 409 && caught.detail === 'terminal_active') throw caught
        writeOperationReservation(actor, { id: operationId, kind: operationKind, created_at: reservedAt, phase: 'reconciling', draft: safeOperationDraft(payload) })
        onAccepted({ id: operationId, kind: operationKind, state: 'queued', phase: 'Проверяем получение запроса', progress_percent: 0, error: null })
        setSelected(null)
      }
    } catch (caught) {
      // Once a POST has an unknown outcome, its UUID is the idempotency key.
      // A failed reauthorization on a retry must not release that reservation
      // or a corrected credential attempt could create a second operation.
      const terminalActive = caught instanceof ApiError && caught.status === 409 && caught.detail === 'terminal_active'
      if (!retryDraft || terminalActive) {
        clearOperationReservation(actor); setReservedOperationId(null)
      }
      if (terminalActive) {
        setError('Завершите активные терминальные сессии и повторите операцию.')
        setPassword(''); setCode('')
      } else if (caught instanceof ApiError && ['capabilities_changed', 'capability_unavailable', 'capabilities_unavailable'].includes(caught.detail ?? '')) {
        setError('Возможности хоста изменились. Список обновлён; подтвердите операцию заново.')
        setConfirmation(''); setPassword(''); setCode('')
        await onRefreshCapabilities()
      } else setError('Операция не запущена. Проверьте пароль и одноразовый код.')
    } finally { onPostingChange(null); setBusy(false) }
  }

  return <div className="rp-system-operations">
    <Panel density="dense" title="Управляемые операции" hint="Доступность поступает с хоста. Произвольные команды и аргументы не принимаются.">
      {capabilities.state !== 'ready' && <Alert tone="warning">Свежий список возможностей хоста недоступен. Все операции заблокированы.</Alert>}
      {context && !contextFresh && <Alert tone="warning">Безопасный контекст хоста устарел. Обновите страницу перед запуском операции.</Alert>}
      {context && <div className="rp-system-operation-context">
        {context.packages.length > 0 && <label className="field"><span className="field-label">Системный пакет</span><select onChange={event => setPackageName(event.target.value)} value={packageName}>{context.packages.map(item => <option key={item} value={item}>{item}</option>)}</select></label>}
        {context.services.length > 0 && <label className="field"><span className="field-label">Сервис</span><select onChange={event => setServiceName(event.target.value)} value={serviceName}>{context.services.map(item => <option key={item} value={item}>{item}</option>)}</select></label>}
        {devices.length > 0 && <label className="field"><span className="field-label">Обнаруженное USB-устройство</span><select onChange={event => setDeviceUuid(event.target.value)} value={deviceUuid}><option value="">Выберите устройство</option>{devices.map(device => <option key={device.device_uuid} value={device.device_uuid}>USB {device.device_uuid.slice(0, 8)}{device.mounted ? ' · подключено' : ''}</option>)}</select></label>}
        {backups.length > 0 && <label className="field"><span className="field-label">Резервная копия</span><select onChange={event => setBackupId(event.target.value)} value={backupId}>{backups.map(backup => <option key={backup.backup_id} value={backup.backup_id}>Копия {backup.backup_id.slice(0, 8)} · {new Intl.NumberFormat('ru-RU').format(backup.bytes)} байт{backup.verified ? ' · проверена' : ''}</option>)}</select></label>}
        {context.rollback_release && <p className="rp-system-context-note">Доступен откат на версию <strong>{context.rollback_release}</strong>.</p>}
      </div>}
      {fresh && capabilities.operations['cleanup-preview']?.available && <fieldset className="rp-system-cleanup-categories">
        <legend>Категории предпросмотра очистки</legend>
        <p>1. Запустите предпросмотр. 2. Проверьте точные цели и объём. 3. Только после этого подтвердите удаление.</p>
        <div className="rp-system-cleanup-categories__options">{cleanupOptions.map(({ category, label }) => <label key={category}>
          <input checked={cleanupCategories.includes(category)} disabled={active} onChange={event => setCleanupCategories(current => event.target.checked
            ? [...current, category].sort((a, b) => cleanupOptions.findIndex(option => option.category === a) - cleanupOptions.findIndex(option => option.category === b))
            : current.filter(item => item !== category))} type="checkbox" />
          <span>{label}</span>
        </label>)}</div>
        <p>Резервные копии и релизы проверяются только при явном выборе.</p>
      </fieldset>}
      <div className="rp-system-operation-list">{operationRows(availableRows)}</div>
      {unavailableRows.length > 0 && <details className="rp-system-unavailable">
        <summary>Недоступные действия ({unavailableRows.length})</summary>
        <div className="rp-system-operation-list">{operationRows(unavailableRows)}</div>
      </details>}
    </Panel>
    {job?.id && job.kind !== 'ota-update' && <Panel density="dense" title="Текущая операция">
      <p className="rp-system-operation-id">{job.id}</p>
      <div className="rp-system-progress"><StatusBadge tone={job.state === 'failed' ? 'critical' : job.state === 'succeeded' ? 'success' : 'info'}>{job.state === 'failed' ? 'Ошибка выполнения' : job.state === 'succeeded' ? 'Завершено' : job.phase || job.state}</StatusBadge><progress aria-label="Прогресс операции" max="100" value={job.progress_percent ?? (job.state === 'succeeded' || job.state === 'failed' ? 100 : 0)} /></div>
      {job.error && !['cleanup_partial', 'image_cleanup_partial', 'image_plan_changed', 'builder_cleanup_partial', 'builder_plan_changed'].includes(job.error) && <Alert tone="error">Операция завершилась ошибкой: {job.error}</Alert>}
      {job.error === 'cleanup_partial' && <Alert tone="error">Очистка прервана. Удалённые файлы: {cleanupResult?.deleted_count ?? 'неизвестно'}. Создайте новый предпросмотр перед повторной очисткой.</Alert>}
      {job.error === 'image_cleanup_partial' && <Alert tone="error">Очистка образов прервана. Подтверждённо удалено меток: {dockerImageResult?.deleted_count ?? 'неизвестно'}. Проверьте последнюю цель и создайте новый предпросмотр.</Alert>}
      {job.error === 'image_plan_changed' && <Alert tone="error">План очистки устарел или образы изменились. Ничего не удалено. Создайте новый предпросмотр образов Docker.</Alert>}
      {job.error === 'builder_cleanup_partial' && <Alert tone="error">Состояние записи BuildKit требует проверки после попытки очистки. Создайте новый предпросмотр; размер освобождённого места пока неизвестен.</Alert>}
      {job.error === 'builder_plan_changed' && <Alert tone="error">План очистки BuildKit устарел или запись изменилась. Ничего не удалено. Создайте новый предпросмотр.</Alert>}
      {cleanupPreview && <div className="rp-system-cleanup-preview">
        <p>Предпросмотр не удалил файлы. Суммарный объём выбранных целей: {new Intl.NumberFormat('ru-RU').format(cleanupPreview.total_bytes)} байт.</p>
        {cleanupPreview.blocked && <Alert tone="warning">План заблокирован проверкой безопасности. Очистка недоступна.</Alert>}
        {cleanupPreview.planned.length ? <ul>{cleanupPreview.planned.map(item => <li key={`${item.category}:${item.path}`}><code>{cleanupRoots[item.category]}{item.path}</code><span>{new Intl.NumberFormat('ru-RU').format(item.bytes)} байт</span></li>)}</ul> : !cleanupPreview.blocked ? <p>Безопасных кандидатов для очистки нет.</p> : null}
      </div>}
      {dockerImagePreview && <div className="rp-system-cleanup-preview">
        <p>Просмотр не удалил образы. Размер кандидатов по Docker: {new Intl.NumberFormat('ru-RU').format(dockerImagePreview.total_reported_bytes)} байт. Общие слои Docker могут уменьшить фактически освобождаемое место.</p>
        {dockerImagePreview.blocked && <Alert tone="warning">Проверка образов заблокирована. Удаление недоступно.</Alert>}
        {dockerImagePreview.unverified_tags > 0 && <Alert tone="warning">Непроверенных меток: {dockerImagePreview.unverified_tags}. Они исключены из кандидатов.</Alert>}
        {dockerImagePreview.planned.length ? <ul>{dockerImagePreview.planned.map(item => <li key={item.tag}><code>{item.tag}</code><span>{new Intl.NumberFormat('ru-RU').format(item.reported_bytes)} байт</span></li>)}</ul> : <p>Безопасных кандидатов среди образов Robopark нет.</p>}
      </div>}
      {dockerImageResult && <div className="rp-system-cleanup-preview">
        {job.state === 'succeeded' && <p>Удалено меток образов: {dockerImageResult.deleted_count}.</p>}
        {dockerImageResult.deleted.length > 0 && <><strong>Подтверждённо удалены</strong><ul>{dockerImageResult.deleted.map(item => <li key={item.tag}><code>{item.tag}</code><span>{new Intl.NumberFormat('ru-RU').format(item.reported_bytes)} байт по Docker</span></li>)}</ul></>}
        {dockerImageResult.uncertain_target && <><strong>Состояние последней метки требует проверки</strong><p><code>{dockerImageResult.uncertain_target.tag}</code></p></>}
      </div>}
      {builderCachePreview && <div className="rp-system-cleanup-preview">
        <p>Просмотр не удалил кэш. Одна выбранная запись собственного BuildKit builder: {new Intl.NumberFormat('ru-RU').format(builderCachePreview.total_reported_bytes)} байт по Docker. Фактически освобождённый объём может отличаться.</p>
        {builderCachePreview.blocked && <Alert tone="warning">Проверка кэша заблокирована. Очистка недоступна.</Alert>}
        {builderCachePreview.other_candidates > 0 && <p>Ещё кандидатов: {builderCachePreview.other_candidates}. Для каждого нужен новый предпросмотр.</p>}
        {builderCachePreview.planned.length ? <ul>{builderCachePreview.planned.map(item => <li key={item.id}><code>{item.id}</code><span>{new Intl.NumberFormat('ru-RU').format(item.reported_bytes)} байт по Docker</span></li>)}</ul> : <p>Безопасных кандидатов в собственном кэше нет.</p>}
      </div>}
      {builderCacheResult && <div className="rp-system-cleanup-preview">
        {job.state === 'succeeded' && <p>Удалена запись BuildKit: {builderCacheResult.deleted_count}. Измерьте занятое место повторно.</p>}
        {builderCacheResult.uncertain_target && <p>Состояние записи BuildKit требует проверки: <code>{builderCacheResult.uncertain_target.id}</code>.</p>}
      </div>}
      {cleanupResult && <div className="rp-system-cleanup-preview">
        {job.state === 'succeeded' && <p>Удалено файлов: {cleanupResult.deleted_count}.</p>}
        {cleanupResult.deleted.length > 0 && <><strong>Подтверждённо удалены</strong><ul>{cleanupResult.deleted.map(item => <li key={`${item.category}:${item.path}`}><code>{cleanupRoots[item.category]}{item.path}</code><span>{new Intl.NumberFormat('ru-RU').format(item.bytes)} байт</span></li>)}</ul></>}
        {cleanupResult.uncertain_target && <><strong>Состояние последней цели требует проверки</strong><p><code>{cleanupRoots[cleanupResult.uncertain_target.category]}{cleanupResult.uncertain_target.path}</code></p></>}
      </div>}
      {operationResult(job, client)}
      {needsRetry && reservation?.draft && <Button onClick={openRetry} type="button">Повторить тот же запрос</Button>}
    </Panel>}
    {resultJobs.some(item => item.id !== job?.id && (item.receipt_state === 'terminal' || item.state === 'succeeded' || item.state === 'failed')) && <Panel density="dense" title="Последние операции">
      <div className="rp-system-operation-history">{resultJobs.filter(item => item.id !== job?.id && (item.receipt_state === 'terminal' || item.state === 'succeeded' || item.state === 'failed')).map(item => <article className="rp-system-operation-history__item" key={item.id}>
        <div className="rp-system-operation-history__head"><strong>{labels[item.kind as HostOperationKind] ?? item.kind}</strong><StatusBadge tone={item.state === 'succeeded' ? 'success' : 'critical'}>{item.state === 'succeeded' ? 'Завершено' : 'Ошибка'}</StatusBadge></div>
        <p className="rp-system-operation-id">{item.id}</p>
        {item.error && <Alert tone="error">Операция завершилась ошибкой: {item.error}</Alert>}
        {operationResult(item, client, true)}
      </article>)}</div>
    </Panel>}
    <Dialog description="Введите фразу без изменений и заново подтвердите личность." dismissible={!busy} initialFocusRef={initialFocus} onOpenChange={open => { if (!open) close() }} open={selected !== null} title="Подтвердить операцию">
      {error && <Alert tone="error">{error}</Alert>}
      {selected === 'docker-image-execute' && dockerImagePreview && <div className="rp-system-cleanup-preview">
        <p>К удалению только эти метки Robopark. Размер по Docker: {new Intl.NumberFormat('ru-RU').format(dockerImagePreview.total_reported_bytes)} байт; общие слои могут уменьшить освобождённое место.</p>
        <ul>{dockerImagePreview.planned.map(item => <li key={item.tag}><code>{item.tag}</code><span>{new Intl.NumberFormat('ru-RU').format(item.reported_bytes)} байт по Docker</span></li>)}</ul>
      </div>}
      {selected === 'builder-cache-execute' && builderCachePreview && <div className="rp-system-cleanup-preview">
        <p>К очистке только эта запись собственного BuildKit builder. Размер по Docker не равен гарантированно освобождённому месту.</p>
        <ul>{builderCachePreview.planned.map(item => <li key={item.id}><code>{item.id}</code><span>{new Intl.NumberFormat('ru-RU').format(item.reported_bytes)} байт по Docker</span></li>)}</ul>
      </div>}
      <label className="field"><span className="field-label">Введите {phrase}</span><input autoComplete="off" disabled={busy} onChange={event => setConfirmation(event.target.value)} ref={initialFocus} value={confirmation} /></label>
      <label className="field"><span className="field-label">Пароль</span><input autoComplete="current-password" disabled={busy} onChange={event => setPassword(event.target.value)} type="password" value={password} /></label>
      <label className="field"><span className="field-label">Код TOTP или восстановления</span><input autoComplete="one-time-code" disabled={busy} onChange={event => setCode(event.target.value)} value={code} /></label>
      <div className="form-actions"><Button busy={busy} disabled={!valid || active} onClick={() => void submit()} type="button">Запустить</Button></div>
    </Dialog>
  </div>
}
