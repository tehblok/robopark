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
  return current === 'ok' ? 'Работает' : current === 'degraded' ? 'Требует внимания' : 'Нет данных'
}
function bytes(value: unknown) {
  return typeof value === 'number' ? `${(value / 1024 ** 3).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} ГБ` : 'Нет данных'
}
function ratio(used: unknown, total: unknown) {
  return typeof used === 'number' && typeof total === 'number' && total > 0 ? Math.round(used / total * 100) : null
}

function ActiveUsersChart({ history }: { history: SystemHistory }) {
  const points = history.active_users.slice(-7)
  const max = Math.max(1, ...points.map(point => point.users))
  return <div><svg aria-label="Активные пользователи за 7 дней" className="rp-system-chart" role="img" viewBox="0 0 280 88">
    <title>Активные пользователи за 7 дней</title>
    {points.map((point, index) => {
      const height = point.users / max * 58
      return <g key={point.date}>
        <rect aria-label={`${point.date}: ${point.users}`} height={height} rx="3" width="24" x={12 + index * 38} y={66 - height} />
        <text x={24 + index * 38} y="82">{point.date.slice(5)}</text>
      </g>
    })}
  </svg><table aria-label="Активные пользователи за 7 дней — значения" className="rp-system-chart-values">
    <thead><tr><th>Дата</th><th>Пользователи</th></tr></thead>
    <tbody>{points.map(point => <tr key={point.date}><td>{point.date}</td><td>{point.users}</td></tr>)}</tbody>
  </table></div>
}

export function SystemMetrics({ summary, history }: { summary: SystemSummary; history: SystemHistory }) {
  const host = record(summary.metrics?.host)
  const disk = record(host.disk)
  const memory = record(host.memory)
  const cpu = record(host.cpu)
  const storage = record(host.storage)
  const requests = record(host.requests)
  const tracker = record(requests.tracker)
  const memoryUse = typeof memory.total_bytes === 'number' && typeof memory.available_bytes === 'number'
    ? memory.total_bytes - memory.available_bytes : null
  const memoryPercent = ratio(memoryUse, memory.total_bytes)
  const diskPercent = typeof disk.total_bytes === 'number' && typeof disk.free_bytes === 'number'
    ? ratio(disk.total_bytes - disk.free_bytes, disk.total_bytes) : null
  return <div className="rp-system-stack">
    {summary.metrics_stale && <Alert tone="warning">Метрики устарели. Действия хоста недоступны до свежего снимка возможностей.</Alert>}
    <section aria-label="Пользователи" className="rp-system-overview">
      <MetricCard label="Сейчас в системе" value={summary.online.total} delta="активность за 2 минуты" />
      <ActiveUsersChart history={history} />
    </section>
    <Panel title="Ресурсы">
      <div className="rp-system-grid">
        <MetricCard label="CPU" value={typeof cpu.load_1m === 'number' ? cpu.load_1m.toLocaleString('ru-RU') : 'Нет данных'} delta={typeof cpu.cores === 'number' ? `${cpu.cores} ядер` : undefined} />
        <MetricCard label="Память" value={memoryPercent == null ? 'Нет данных' : `${memoryPercent}%`} delta={`${bytes(memory.available_bytes)} доступно`} />
        <MetricCard label="Диск" value={diskPercent == null ? 'Нет данных' : `${diskPercent}%`} delta={`${bytes(disk.free_bytes)} свободно`} />
        <MetricCard label="PostgreSQL" value={state(host.postgresql)} />
        <MetricCard label="Контейнеры" value={state(host.container)} />
        <MetricCard label="Сеть" value={state(host.internet)} />
        <MetricCard label="Wi‑Fi" value={state(host.wifi)} />
      </div>
    </Panel>
    <Panel title="Очереди и интеграции">
      <div className="rp-system-grid">
        <MetricCard label="Очередь" value={summary.sync.pending_action_count} delta={`${summary.sync.needs_attention_count} требуют внимания`} tone={summary.sync.needs_attention_count ? 'warning' : 'success'} />
        <MetricCard label="Worker" value={summary.sync.worker_lease_state === 'active' ? 'Работает' : summary.sync.worker_lease_state === 'stale' ? 'Задержка' : 'Нет данных'} />
        <MetricCard label="Tracker" value={typeof tracker.errors !== 'number' && !summary.sync.last_error ? 'Нет данных' : typeof tracker.errors === 'number' && tracker.errors > 0 ? 'Есть ошибки' : summary.sync.last_error ? 'Есть ошибки' : 'Работает'} delta={summary.sync.cursor_age_seconds == null ? 'Курсор неизвестен' : `Курсор: ${summary.sync.cursor_age_seconds} с`} />
        <MetricCard label="Tuna" value={state(host.tuna)} />
        <MetricCard label="Хранилище" value={bytes(disk.free_bytes)} delta={typeof storage.bytes_to_reclaim === 'number' ? `К очистке: ${bytes(storage.bytes_to_reclaim)}` : 'Состояние очистки неизвестно'} />
      </div>
      {summary.sync.needs_attention_count > 0 && <p><Link to="/work?sync=needs_attention">Открыть ошибки синхронизации</Link> — безопасный повтор доступен в карточке задачи при наличии права.</p>}
    </Panel>
    <Panel title="Состояние очистки и резервных копий">
      <div className="rp-system-status-row">
        <StatusBadge tone={record(host.backup).overdue === true ? 'warning' : record(host.backup).overdue === false ? 'success' : 'neutral'}>Резервная копия: {record(host.backup).overdue === true ? 'просрочена' : record(host.backup).overdue === false ? 'проверена' : 'Неизвестно'}</StatusBadge>
        <StatusBadge tone={storage.cleanup_failed === true ? 'critical' : storage.cleanup_failed === false ? 'success' : 'neutral'}>Очистка: {storage.cleanup_failed === true ? 'ошибка' : storage.cleanup_failed === false ? 'без ошибок' : 'Неизвестно'}</StatusBadge>
      </div>
    </Panel>
  </div>
}
