import { Link } from 'react-router-dom'
import { Alert, Panel } from '../../components/PageShell'
import { MetricCard } from '../../design-system/data/MetricCard'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import type { SystemHistory, SystemSummary } from '../../opsApi'

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}
function state(value: unknown) {
  const current = record(value).state
  return current === 'ok' ? 'Работает' : current === 'degraded' ? 'Требует внимания' : 'Проверка не получена'
}
function bytes(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? measuredBytes(value) : 'Нет данных'
}
function isMeasuredBytes(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}
function measuredBytes(value: unknown) {
  if (!isMeasuredBytes(value)) return 'Не измерено'
  if (value < 1024) return `${value} Б`
  const unit = value < 1024 ** 2 ? 'КБ' : value < 1024 ** 3 ? 'МБ' : 'ГБ'
  const divisor = unit === 'КБ' ? 1024 : unit === 'МБ' ? 1024 ** 2 : 1024 ** 3
  return `${(value / divisor).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} ${unit}`
}
function ratio(used: unknown, total: unknown) {
  return typeof used === 'number' && typeof total === 'number' && total > 0 ? Math.round(used / total * 100) : null
}
function historyDate(value: string, compact = false) {
  const date = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  if (!date) return value
  const dayAndMonth = `${date[3]}.${date[2]}`
  return compact ? dayAndMonth : `${dayAndMonth}.${date[1]}`
}

const storageCategories = [
  { key: 'logs', label: 'Журналы Robopark' },
  { key: 'diagnostics', label: 'Диагностика' },
  { key: 'live_merge', label: 'Данные синхронизации' },
  { key: 'report_attachments', label: 'Вложения репортов' },
  { key: 'tracker_uploads', label: 'Файлы Tracker' },
  { key: 'backups', label: 'Резервные копии' },
  { key: 'scheduled_backups', label: 'Локальные снимки' },
  { key: 'releases', label: 'Релизы' },
  { key: 'ota_uploads', label: 'Загрузки OTA' },
  { key: 'ota_cache', label: 'Кэш OTA' },
  { key: 'journald', label: 'Журнал systemd' },
  { key: 'docker_images', label: 'Образы Docker на хосте' },
  { key: 'buildkit_cache', label: 'Кэш Docker Engine (без отдельного Buildx)' },
  { key: 'robopark_buildkit_reported', label: 'BuildKit Robopark (отчётный объём)' },
  { key: 'robopark_buildkit_private_reclaimable', label: 'Из него приватный кэш, доступный для очистки' },
  { key: 'docker_volumes', label: 'Тома Docker (данные и служебные хранилища)' },
  { key: 'postgresql_data', label: 'Данные PostgreSQL' },
] as const

function ActiveUsersChart({ history }: { history: SystemHistory }) {
  const points = history.active_users.slice(-7)
  if (!points.length) return <p className="rp-system-history-empty">История активности ещё не собрана.</p>
  const max = Math.max(1, ...points.map(point => point.users))
  return <div className={points.length === 1 ? 'rp-system-history-single' : undefined}>{points.length === 1
    ? <p>История за один день. График появится после следующего дня.</p>
    : <svg aria-label="Активные пользователи за 7 дней" className="rp-system-chart" role="img" viewBox="0 0 280 88">
    <title>Активные пользователи за 7 дней</title>
    {points.map((point, index) => {
      const height = point.users / max * 58
      const center = 280 * (index + 0.5) / points.length
      return <g key={point.date}>
        <rect height={height} rx="3" width="24" x={center - 12} y={66 - height} />
        <text x={center} y="82">{historyDate(point.date, true)}</text>
      </g>
    })}
  </svg>}<table aria-label="Активные пользователи за 7 дней — значения" className="rp-system-chart-values">
    <thead><tr><th>Дата</th><th>Пользователи</th></tr></thead>
    <tbody>{points.map(point => <tr key={point.date}><td>{historyDate(point.date)}</td><td>{point.users}</td></tr>)}</tbody>
  </table></div>
}

export function SystemMetrics({ summary, history }: { summary: SystemSummary; history: SystemHistory | null }) {
  const host = record(summary.metrics?.host)
  const disk = record(host.disk)
  const memory = record(host.memory)
  const cpu = record(host.cpu)
  const storage = record(host.storage)
  const cleanupFailed = storage.cleanup_failed === true || storage.api_cleanup_failed === true
  const cleanupBusy = storage.cleanup_busy === true && !cleanupFailed
  const builderBudget = record(storage.builder_cache_budget)
  const backup = record(host.backup)
  const categoryBytes = record(storage.category_bytes)
  const measuredStorage = storageCategories.filter(({ key }) => isMeasuredBytes(categoryBytes[key]))
  const missingStorage = storageCategories.filter(({ key }) => !isMeasuredBytes(categoryBytes[key]))
  const requests = record(host.requests)
  const tracker = record(requests.tracker)
  const memoryUse = typeof memory.total_bytes === 'number' && typeof memory.available_bytes === 'number'
    ? memory.total_bytes - memory.available_bytes : null
  const memoryPercent = ratio(memoryUse, memory.total_bytes)
  const diskPercent = typeof disk.total_bytes === 'number' && typeof disk.free_bytes === 'number'
    ? ratio(disk.total_bytes - disk.free_bytes, disk.total_bytes) : null
  const workerHealth = summary.worker_health
  const workerStatus = workerHealth === 'worker_healthy' ? 'Работает'
    : workerHealth === 'worker_heartbeat_stale' ? 'Heartbeat просрочен'
    : workerHealth === 'worker_heartbeat_missing' ? 'Heartbeat отсутствует'
    : workerHealth === 'worker_metric_stale' ? 'Метрика просрочена'
    : workerHealth === 'worker_metric_future' ? 'Ошибка времени метрики'
    : 'Метрика отсутствует'
  const hostMissing = Object.keys(host).length === 0
  const diskUnavailable = disk.source_state === 'unavailable'
  const agentSnapshotUnavailable = host.host_health_source_state === 'unavailable'
  const unmeasuredHostValue = hostMissing ? 'Снимок отсутствует' : 'Не измерено'
  const trackerState = summary.tracker?.state
  const trackerValue = trackerState === 'not_configured' ? 'Не настроен'
    : trackerState === 'credential_unavailable' ? 'Ошибка ключа'
    : trackerState !== 'configured' ? unmeasuredHostValue
    : (typeof tracker.errors === 'number' && tracker.errors > 0) || summary.sync.last_error ? 'Есть ошибки'
      : summary.sync.last_success_at === null ? 'Проверка не выполнена'
        : summary.sync.cursor_age_seconds === null ? 'Возраст неизвестен'
          : summary.sync.cursor_age_seconds > 300 ? 'Проверка устарела' : 'Работает'
  const trackerDelta = trackerState === 'not_configured' ? 'Добавьте токен Tracker в настройках'
    : trackerState === 'credential_unavailable' ? 'Проверьте ключ шифрования'
      : summary.sync.cursor_age_seconds == null ? 'Курсор неизвестен'
        : `Курсор: ${summary.sync.cursor_age_seconds} с`
  const diskPercentLabel = diskUnavailable ? 'Ошибка чтения'
    : diskPercent == null ? unmeasuredHostValue
    : diskPercent === 100 && typeof disk.free_bytes === 'number' && disk.free_bytes > 0 ? '>99%'
      : `${diskPercent}%`
  const serviceChecksMissing = !hostMissing && [host.container, host.tuna, host.internet]
    .every(value => !['ok', 'degraded'].includes(String(record(value).state)))
  const backupVerified = typeof backup.verified_at === 'number' && Number.isFinite(backup.verified_at) && backup.verified_at > 0
  const backupLabel = backupVerified
    ? backup.overdue === true ? 'просрочена' : backup.overdue === false ? 'проверена' : 'Неизвестно'
    : backup.verified_at === null ? 'не подтверждена' : 'Неизвестно'
  const backupTone = backupLabel === 'проверена' ? 'success' : backupLabel === 'Неизвестно' ? 'neutral' : 'warning'
  return <div className="rp-system-stack" id="system-status">
    {summary.metrics_stale && <Alert tone="warning">Метрики устарели. Сведения о ресурсах могут быть неактуальны; проверьте сбор метрик worker.</Alert>}
    {hostMissing && <Alert tone="error">Снимок хоста отсутствует. Проверьте сбор метрик и связь API с host bridge.</Alert>}
    {diskUnavailable && <Alert tone="error">Не удалось измерить диск API. Проверьте доступность каталога данных на хосте.</Alert>}
    {agentSnapshotUnavailable && <Alert tone="error">Снимок host agent отсутствует или повреждён. Проверьте службу host agent и её журнал.</Alert>}
    {serviceChecksMissing && <Alert tone="warning">Проверки служб хоста отсутствуют или устарели. Проверьте host-health и сбор метрик.</Alert>}
    {workerHealth === 'worker_heartbeat_missing' && <Alert tone="error">Heartbeat worker не зарегистрирован. Проверьте запуск worker и его журнал.</Alert>}
    {workerHealth === 'worker_heartbeat_stale' && <Alert tone="error">Heartbeat worker просрочен. Проверьте журнал worker и подключение к базе данных.</Alert>}
    {workerHealth === 'worker_metric_missing' && <Alert tone="error">Метрика worker отсутствует после запуска. Проверьте сбор метрик и журнал worker.</Alert>}
    {workerHealth === 'worker_metric_stale' && <Alert tone="error">Метрика worker просрочена. Проверьте сбор метрик и журнал worker.</Alert>}
    {workerHealth === 'worker_metric_future' && <Alert tone="error">Время метрики worker опережает сервер. Проверьте часы хоста и сбор метрик.</Alert>}
    {backup.last_attempt_failed === true && <Alert tone="error">Последняя попытка резервного копирования завершилась ошибкой.</Alert>}
    {builderBudget.blocked === true && <Alert tone="error">Ограничение кэша сборки Docker не выполнено. Проверьте журнал host agent и доступность собственного BuildKit builder Robopark.</Alert>}
    <section aria-label="Пользователи" className="rp-system-overview">
      <MetricCard label="Сейчас в системе" value={summary.online.total} delta="активность за 2 минуты" />
      {history ? <ActiveUsersChart history={history} /> : <p>История активности недоступна</p>}
    </section>
    <Panel density="dense" title="Ресурсы">
      <div className="rp-system-grid">
        <MetricCard label="Нагрузка CPU · 1 мин" value={typeof cpu.load_1m === 'number' ? cpu.load_1m.toLocaleString('ru-RU') : unmeasuredHostValue} delta={typeof cpu.cores === 'number' ? `${cpu.cores} ядер` : undefined} />
        <MetricCard label="Память" value={memoryPercent == null ? unmeasuredHostValue : `${memoryPercent}%`} delta={typeof memory.available_bytes === 'number' ? `${bytes(memory.available_bytes)} доступно` : undefined} />
        <MetricCard label="Диск" value={diskPercentLabel} delta={typeof disk.free_bytes === 'number' ? `${bytes(disk.free_bytes)} свободно` : undefined} />
        <MetricCard label="PostgreSQL" value={state(host.postgresql)} />
        <MetricCard label="Контейнеры" value={state(host.container)} />
        <MetricCard label="Сеть" value={state(host.internet)} />
        <MetricCard label="Wi‑Fi" value={state(host.wifi)} />
      </div>
    </Panel>
    <Panel density="dense" title="Очереди и интеграции">
      <div className="rp-system-grid">
        <MetricCard label="Очередь" value={summary.sync.pending_action_count} delta={`${summary.sync.needs_attention_count} требуют внимания`} tone={summary.sync.needs_attention_count ? 'warning' : 'success'} />
        <MetricCard label="Worker" value={workerStatus} tone={workerHealth === 'worker_healthy' ? 'success' : 'critical'} />
        <MetricCard label="Tracker" value={trackerValue} delta={trackerDelta} />
        <MetricCard label="Tuna" value={state(host.tuna)} />
        <MetricCard label="Хранилище" value={diskUnavailable ? 'Ошибка чтения' : typeof disk.free_bytes === 'number' ? bytes(disk.free_bytes) : unmeasuredHostValue} delta={typeof storage.bytes_to_reclaim === 'number' ? `К очистке: ${bytes(storage.bytes_to_reclaim)}` : 'Объём к освобождению не измерен'} />
      </div>
      {summary.sync.needs_attention_count > 0 && <p><Link to="/work?sync=needs_attention">Открыть ошибки синхронизации</Link> — безопасный повтор доступен в карточке задачи при наличии права.</p>}
    </Panel>
    <Panel density="dense" title="Состояние очистки и резервных копий">
      <div className="rp-system-status-row">
        <StatusBadge tone={backupTone}>Резервная копия: {backupLabel}</StatusBadge>
        <StatusBadge tone={cleanupFailed ? 'critical' : cleanupBusy ? 'warning' : storage.cleanup_failed === false ? 'success' : 'neutral'}>Очистка: {cleanupFailed ? 'ошибка' : cleanupBusy ? 'отложена' : storage.cleanup_failed === false ? 'без ошибок' : 'Неизвестно'}</StatusBadge>
        {storage.space_pressure === true && <StatusBadge tone="warning">Место: недостаточно</StatusBadge>}
      </div>
    </Panel>
    <div id="system-storage" className="rp-system-anchor">
    <Panel density="dense" title="Занятое место на хосте">
      {storage.inventory_failed === true && <Alert tone="warning">Не удалось измерить хранилище хоста. Проверьте журнал host agent; отсутствующие размеры показаны как «Не измерено».</Alert>}
      {storage.inventory_stale === true && <Alert tone="warning">Измерение хранилища устарело. Проверьте запуск host agent; старые размеры OTA, Docker, резервных копий, релизов и PostgreSQL скрыты.</Alert>}
      {storage.cleanup_stale === true && <Alert tone="warning">Измерение журналов и диагностики устарело. Проверьте запуск host agent; старые размеры скрыты.</Alert>}
      {measuredStorage.length ? <dl className="rp-system-storage-breakdown">
        {measuredStorage.map(({ key, label }) => <div key={key}><dt>{label}</dt><dd>{measuredBytes(categoryBytes[key])}</dd></div>)}
      </dl> : <p role="status">Измерения занятого места пока не получены.</p>}
      {missingStorage.length ? <details className="rp-system-storage-missing">
        <summary>Не измерено: {missingStorage.length} категорий</summary>
        <dl className="rp-system-storage-breakdown">
          {missingStorage.map(({ key, label }) => <div key={key}><dt>{label}</dt><dd>Не измерено</dd></div>)}
        </dl>
      </details> : null}
      <p>Размеры категорий могут пересекаться; складывать их нельзя. Тома Docker и PostgreSQL не удаляются.</p>
      {categoryBytes.buildkit_cache === 0 && <p>0 Б в строке «Кэш Docker Engine» не означает, что отдельный Buildx пуст. Его объём показан отдельно в строках BuildKit Robopark.</p>}
      <details className="rp-system-storage-explainer">
        <summary>Как читать размеры и что входит в очистку</summary>
        <p>Локальные снимки хранятся отдельно от резервных копий для USB. Ежедневный таймер оставляет до 14 снимков после успешной новой копии; ручной предпросмотр очистки ниже показывает другие категории и не включает локальные снимки.</p>
        <p>{typeof categoryBytes.postgresql_data === 'number'
          ? 'Том PostgreSQL измерен отдельно и уже входит в общий объём томов Docker.'
          : 'Объём PostgreSQL не измерен отдельно от томов.'} Цифры Docker включают все проекты хоста и могут пересекаться с релизами; складывать строки нельзя. Размеры записей BuildKit могут пересекаться между собой и с образами; приватный кэш — оценка, а не обещание освобождённого места. Автоматическая очистка кэша ограничена сборщиком Robopark; кэш других проектов и тома не удаляются.</p>
        <p>Тома Docker могут хранить как данные, так и служебные хранилища; этот объём не считается доступным для очистки.</p>
      </details>
    </Panel>
    </div>
  </div>
}
