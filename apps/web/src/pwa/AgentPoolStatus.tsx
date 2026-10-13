import { useEffect, useState } from 'react'
import { api, type AgentPoolStatus as PoolStatus } from '../api'

const statusLabel: Record<PoolStatus['devices'][number]['status'], string> = {
  ready: 'готов', busy: 'занят', paused: 'приостановлен', stale: 'нет свежих данных', offline: 'не в сети',
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
      {status.devices.length ? <ul>{status.devices.slice(0, 12).map(device => (
        <li key={device.id}><span>{device.label}</span><small>{statusLabel[device.status]} · {device.inflight}/{device.capacity}{device.queued ? ` · очередь ${device.queued}` : ''}{device.latency_ms == null ? '' : ` · ${Math.round(device.latency_ms)} мс`}</small></li>
      ))}</ul> : <p>Подключённых Mac пока нет.</p>}
      {status.devices.length > 12 ? <p>Показаны первые 12 из {status.devices.length} устройств.</p> : null}
    </section>
  )
}
