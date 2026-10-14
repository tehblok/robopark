import { useEffect, useState } from 'react'
import { api, type ComputePoolStatus as PoolStatus } from '../api'
import { NetworkGraph } from './AgentPoolStatus'
import { formatBytes, ownerName, statusLabel } from './poolFormatting'

const routeNames: Record<string, string> = {
  image_resize_v1: 'Фото', csv_render_v1: 'Таблицы',
  'inventory:rover_card': 'Карточки Inventory', 'inventory:rover_installations': 'Комплектация Inventory',
  'tracker:issue_read': 'Задачи Tracker', 'tracker:issues_search': 'Поиск Tracker',
  'tracker:comments_read': 'Комментарии Tracker', 'tracker:history_read': 'История Tracker',
  'tracker:issues_count': 'Количество задач', 'tracker:transitions_read': 'Переходы Tracker',
  'tracker:queue_read': 'Очереди Tracker', 'tracker:queue_components_read': 'Компоненты Tracker',
  'tracker:comment_create': 'Отправка комментария', 'tracker:issue_patch': 'Изменение задачи',
  'tracker:transition_execute': 'Изменение статуса', 'tracker:attachment_upload': 'Отправка вложения',
}

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
    <p>Доступ и исполнитель выбираются для каждой операции отдельно. Без Mac сайт и бот используют настроенные серверные интеграции; доступ сервера к каждой системе проверяется при обращении.</p>
    {status.devices.length ? <><NetworkGraph status={status} /><ul className="rp-agent-list">{status.devices.map(device => <li key={device.id} tabIndex={0}>
      <span><strong>{device.label}</strong><small>{ownerName(device)}</small></span>
      <small>{statusLabel[device.status]} · {device.active_jobs}/{device.capacity} задач</small>
      {device.cpu_percent != null || device.memory_percent != null ? <small>{device.cpu_percent == null ? '' : `CPU ${Math.round(device.cpu_percent)}%`}{device.cpu_percent != null && device.memory_percent != null ? ' · ' : ''}{device.memory_percent == null ? '' : `память ${Math.round(device.memory_percent)}%`}</small> : null}
      <small>{device.completed} выполнено · {device.failed} сбоев</small>
      {device.supports.includes('corporate_tracker_read_v1') ? <small>Tracker: личный вход подтверждён · {device.corporate_read_capacity ?? 0} слотов чтения</small> : null}
      {device.supports.includes('corporate_tracker_write_v1') ? <small>Tracker: запись · {device.corporate_write_capacity ?? 0} слотов</small> : null}
      {device.corporate_inventory_status ? <small>Inventory: {device.corporate_inventory_status === 'ready' ? 'доступ проверен' : ['denied', 'access_denied'].includes(device.corporate_inventory_status) ? 'нет доступа' : 'доступ ещё не подтверждён'}</small> : null}
      {Object.entries(device.route_health ?? {}).filter(([key]) => routeNames[key]).map(([key, route]) => <small key={key}>
        {routeNames[key]}: {route.latency_ms == null ? 'время ответа ещё не измерено' : `${route.latency_ms} мс`}
        {route.retry_after_seconds > 0 ? ` · повторная проверка через ${route.retry_after_seconds} с` : ''}
      </small>)}
    </li>)}</ul></> : <p>После входа в Mac-приложение устройство появится здесь автоматически.</p>}
    {status.devices.length > 12 ? <p>На графе показаны 12 из {status.devices.length} устройств; полный список ниже.</p> : null}
  </section>
}
