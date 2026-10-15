import { useEffect, useState } from 'react'
import { api, type AgentPoolStatus as PoolStatus } from '../api'
import './AgentPoolStatus.css'
import { deviceStatusLabel, formatBytes, ownerName, type GraphDevice } from './poolFormatting'

export function NetworkGraph({ status }: { status: { devices: GraphDevice[] } }) {
  const displayed = status.devices.slice(0, 12)
  const owners = Array.from(new Map(displayed.map(device => [device.owner_user_id ?? ownerName(device), ownerName(device)])).entries())
  let nextRowY = 36
  const groups = owners.map(([ownerId, name]) => {
    const devices = displayed.filter(device => (device.owner_user_id ?? ownerName(device)) === ownerId)
    const deviceRows = devices.map((device, index) => ({ device, y: nextRowY + index * 44 }))
    const ownerY = deviceRows.length
      ? Math.min((deviceRows[0].y + deviceRows[deviceRows.length - 1].y) / 2, deviceRows[0].y + 80)
      : nextRowY
    nextRowY += Math.max(1, devices.length) * 44 + 12
    return { ownerId, name, ownerY, deviceRows }
  })
  const graphHeight = Math.max(260, nextRowY + 24)
  const serverY = Math.min(130, graphHeight / 2)
  return (
    <div className="rp-agent-graph-viewport">
      <svg className="rp-agent-graph" viewBox={`0 0 720 ${graphHeight}`} role="img" aria-label="Граф сети: сервер, сотрудники и их Mac" tabIndex={0}>
        <title>Граф сети: сервер, сотрудники и их Mac</title>
        <rect x="20" y={serverY - 28} width="120" height="56" rx="14" className="rp-agent-graph__server" />
        <text x="80" y={serverY - 4} textAnchor="middle">Сервер</text>
        <text x="80" y={serverY + 16} textAnchor="middle" className="rp-agent-graph__muted">Robopark test</text>
        {groups.map(({ ownerId, name, ownerY, deviceRows }) => <g key={String(ownerId)}>
          <title>{name}: {deviceRows.map(({ device }) => device.label).join(', ')}</title>
          <line x1="140" y1={serverY} x2="270" y2={ownerY} />
          <rect x="270" y={ownerY - 17} width="150" height="34" rx="10" className="rp-agent-graph__owner" />
          <text x="345" y={ownerY + 5} textAnchor="middle">{name.slice(0, 22)}</text>
          {deviceRows.map(({ device, y }) => <g key={device.id}>
            <title>{device.label}: {deviceStatusLabel(device)}</title>
            <line x1="420" y1={ownerY} x2="530" y2={y} />
            <circle cx="548" cy={y} r="12" className={`rp-agent-graph__device rp-agent-graph__device--${device.status}`} />
            <text x="568" y={y + 5}>{device.label.slice(0, 18)}</text>
          </g>)}
        </g>)}
      </svg>
    </div>
  )
}

export function AgentPoolStatus({ load = api.agentPoolStatus }: { load?: () => Promise<PoolStatus> }) {
  const [status, setStatus] = useState<PoolStatus | null>(null)
  const [unavailable, setUnavailable] = useState(false)
  useEffect(() => {
    let active = true
    let timer: number | undefined
    const refresh = () => {
      window.clearTimeout(timer)
      if (navigator.onLine === false) return
      void load().then(value => { if (active) { setStatus(value); setUnavailable(false) } }).catch(() => { if (active) setUnavailable(true) })
        .finally(() => { if (active && navigator.onLine !== false) timer = window.setTimeout(refresh, 20_000) })
    }
    const pause = () => window.clearTimeout(timer)
    window.addEventListener('online', refresh)
    window.addEventListener('offline', pause)
    refresh()
    return () => { active = false; window.clearTimeout(timer); window.removeEventListener('online', refresh); window.removeEventListener('offline', pause) }
  }, [load])
  if (unavailable) return <p>Статус общего пула временно недоступен.</p>
  if (!status) return <p>Проверяем общий пул Mac…</p>
  if (!status.enabled) return <p>Распределение запросов по Mac выключено на сервере.</p>
  return (
    <section aria-label="Общий пул Mac" className="rp-sync-center__pool">
      <strong>Общий пул Mac</strong>
      <p>{status.summary.ready} готово из {status.summary.connected} подключённых · {status.summary.inflight} из {status.summary.capacity} слотов занято</p>
      <p>{status.summary.queued} в очереди · {status.summary.upstream_dispatches} запросов к Emergency · {status.summary.upstream_backpressure} ожиданий лимита</p>
      {status.devices.length ? <><NetworkGraph status={status} /><ul className="rp-agent-list">{status.devices.map(device => (
        <li key={device.id} tabIndex={0}>
          <span><strong>{device.label}</strong><small>{ownerName(device)}</small></span>
          <small>{deviceStatusLabel(device)} · {device.inflight}/{device.capacity}{device.queued ? ` · очередь ${device.queued}` : ''}{device.latency_ms == null ? '' : ` · связь ${Math.round(device.latency_ms)} мс`}</small>
          {device.fresh !== false && (device.cpu_percent != null || device.memory_percent != null) ? <small>{device.cpu_percent == null ? '' : `CPU ${Math.round(device.cpu_percent)}%`}{device.cpu_percent != null && device.memory_percent != null ? ' · ' : ''}{device.memory_percent == null ? '' : `память ${Math.round(device.memory_percent)}%`}{device.thermal_state ? ` · нагрев ${device.thermal_state}` : ''}</small> : null}
          {device.throughput_mbps != null ? <small>{device.throughput_mbps.toLocaleString('ru-RU', { maximumFractionDigits: 1 })} Мбит/с скорость ответов{device.response_latency_ms == null ? '' : ` · ${Math.round(device.response_latency_ms)} мс ответ`}</small> : null}
          {(device.compression_saved_bytes ?? 0) > 0 || (device.cache_hits ?? 0) > 0 ? <small>экономия {formatBytes(device.compression_saved_bytes ?? 0)} · кэш {device.cache_hits ?? 0}</small> : null}
          {device.latency_ms == null && device.throughput_mbps == null && device.cpu_percent == null && device.memory_percent == null ? <small>Измерений пока нет.</small> : null}
        </li>
      ))}</ul></> : <p>Зарегистрированных Mac пока нет.</p>}
      {status.devices.length > 12 ? <p>На графе показаны 12 из {status.devices.length} устройств; полный список ниже.</p> : null}
    </section>
  )
}
