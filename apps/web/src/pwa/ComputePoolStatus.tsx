import { useEffect, useState } from 'react'
import { api, type ComputePoolStatus as PoolStatus } from '../api'
import { NetworkGraph } from './AgentPoolStatus'
import { formatBytes, ownerName, statusLabel } from './poolFormatting'

export function ComputePoolStatus({ load = api.computePoolStatus }: { load?: () => Promise<PoolStatus> }) {
  const [status, setStatus] = useState<PoolStatus | null>(null)
  const [unavailable, setUnavailable] = useState(false)
  const [offline, setOffline] = useState(navigator.onLine === false)
  useEffect(() => {
    let active = true
    let pending = false
    let timer: number | undefined
    const canPoll = () => navigator.onLine !== false && document.visibilityState !== 'hidden'
    const refresh = async () => {
      window.clearTimeout(timer)
      if (!active || pending || !canPoll()) return
      pending = true
      let delay = 30_000
      try {
        const value = await load()
        if (active) { setStatus(value); setUnavailable(false) }
        if (value.summary.inflight) delay = 5_000
      } catch { if (active) setUnavailable(true); delay = 60_000 }
      finally {
        pending = false
        if (active && canPoll()) timer = window.setTimeout(() => void refresh(), delay)
      }
    }
    const change = () => { setOffline(navigator.onLine === false); void refresh() }
    window.addEventListener('online', change)
    window.addEventListener('offline', change)
    document.addEventListener('visibilitychange', change)
    void refresh()
    return () => {
      active = false; window.clearTimeout(timer)
      window.removeEventListener('online', change); window.removeEventListener('offline', change)
      document.removeEventListener('visibilitychange', change)
    }
  }, [load])
  if (offline) return <p>Нет связи. Фото обрабатываются на этом устройстве; состояние Mac обновится после подключения.</p>
  if (unavailable) return <p>Не удалось обновить состояние вычислений. Обработка на этом устройстве остаётся доступна.</p>
  if (!status) return <p>Проверяем свободные Mac…</p>
  if (!status.enabled) return <p>Вычисления на свободных Mac выключены на сервере.</p>
  const metrics = status.summary
  return <section aria-label="Вычисления на Mac" className="rp-sync-center__pool">
    <strong>Вычисления на Mac</strong>
    <p>{metrics.ready} свободно из {metrics.connected} подключённых · {metrics.inflight} из {metrics.capacity} слотов занято</p>
    <p>{metrics.completed} задач выполнено · {metrics.failed} сбоев · {metrics.direct_completed} прямых передач</p>
    <p>Напрямую между устройствами: {formatBytes(metrics.direct_input_bytes + metrics.direct_output_bytes)} · согласование через сервер: {formatBytes(metrics.signaling_bytes)}</p>
    <small>Счётчики текущих подключений. Фото сначала обрабатываются на вашем устройстве. Свободные Mac помогают с фото и выгрузками; итоговые данные сохраняются на сервере.</small>
    {status.devices.length ? <><NetworkGraph status={status} /><ul className="rp-agent-list">{status.devices.map(device => <li key={device.id} tabIndex={0}>
      <span><strong>{device.label}</strong><small>{ownerName(device)}</small></span>
      <small>{statusLabel[device.status]} · {device.active_jobs}/{device.capacity} задач</small>
      {device.cpu_percent != null || device.memory_percent != null ? <small>{device.cpu_percent == null ? '' : `CPU ${Math.round(device.cpu_percent)}%`}{device.cpu_percent != null && device.memory_percent != null ? ' · ' : ''}{device.memory_percent == null ? '' : `память ${Math.round(device.memory_percent)}%`}</small> : null}
      <small>{device.completed} выполнено · {device.failed} сбоев</small>
    </li>)}</ul></> : <p>После входа в Mac-приложение устройство появится здесь автоматически.</p>}
    {status.devices.length > 12 ? <p>На графе показаны 12 из {status.devices.length} устройств; полный список ниже.</p> : null}
  </section>
}
