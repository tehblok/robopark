import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Panel } from '../../components/PageShell'
import { Button } from '../../design-system/actions/Button'
import { FormField } from '../../design-system/forms/FormField'
import {
  diagnosticClient,
  DiagnosticsUnavailableError,
  type DiagnosticClient,
  type DiagnosticRequest,
  type DiagnosticService,
  type DiagnosticSnapshot,
} from './deviceClient'

const serviceLabels: Record<DiagnosticService, string> = {
  server: 'Сервер TEST', emergency: 'Emergency', inventory: 'Inventory', tracker: 'Tracker',
}
const statusLabels: Record<string, string> = {
  ok: 'успешно', success: 'успешно', running: 'выполняется', error: 'ошибка', failed: 'ошибка', skipped: 'пропущено',
}

function resultText(result: DiagnosticSnapshot['results'][number]) {
  return [
    serviceLabels[result.service as DiagnosticService] ?? result.service,
    result.route,
    result.robot ? `робот ${result.robot}` : null,
    statusLabels[result.status] ?? result.status,
    result.httpStatus === undefined ? null : `HTTP ${result.httpStatus}`,
    result.code,
    result.elapsedMs === undefined ? null : `${result.elapsedMs} мс`,
  ].filter(Boolean).join(' · ')
}

export function DiagnosticPanel({ client = diagnosticClient }: { client?: DiagnosticClient }) {
  const [service, setService] = useState<DiagnosticService>('server')
  const [route, setRoute] = useState<DiagnosticRequest['route']>('direct')
  const [robot, setRobot] = useState('1170')
  const [snapshot, setSnapshot] = useState<DiagnosticSnapshot | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [expanded, setExpanded] = useState(false)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])

  const refresh = useCallback(async () => {
    try {
      const next = await client.snapshot()
      if (mounted.current) { setSnapshot(next); setMessage('') }
    } catch (error) {
      if (!mounted.current) return
      setMessage(error instanceof DiagnosticsUnavailableError
        ? 'Диагностика доступна в новой тестовой сборке.'
        : 'Не удалось обновить снимок диагностики.')
    }
  }, [client])

  useEffect(() => {
    if (!snapshot?.running || !expanded) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const poll = async () => {
      if (document.visibilityState !== 'hidden') await refresh()
      if (!cancelled) timer = setTimeout(() => { void poll() }, 1_500)
    }
    timer = setTimeout(() => { void poll() }, 1_500)
    return () => { cancelled = true; if (timer) clearTimeout(timer) }
  }, [expanded, refresh, snapshot?.running])

  const validRobot = /^\d+$/.test(robot) && Number(robot) >= 1000 && Number(robot) <= 3000
  const run = async () => {
    if (service !== 'server' && !validRobot) return
    setBusy(true); setMessage('')
    try {
      const next = await client.run({ service, route: service === 'server' ? 'direct' : route, robot: service === 'server' ? '1170' : robot })
      if (mounted.current) setSnapshot(next)
    } catch (error) {
      if (mounted.current) setMessage(error instanceof DiagnosticsUnavailableError
        ? 'Диагностика доступна в новой тестовой сборке.'
        : 'Проверка не запустилась. Обновите снимок и повторите.')
    } finally { if (mounted.current) setBusy(false) }
  }

  const lastEvent = snapshot?.events.at(-1)
  return <Panel title="Диагностика TEST" hint="Только чтение. Проверка не отправляет команды роботам и не меняет корпоративные данные.">
    <details className="rp-device-diagnostics" onToggle={event => {
      setExpanded(event.currentTarget.open)
      if (event.currentTarget.open) void refresh()
    }}>
      <summary>Открыть конструктор диагностики</summary>
      <div className="rp-device-diagnostics__body">
        <div className="rp-device-diagnostics__fields">
          <FormField id="diagnostic-service" label="Сервис"><select value={service} onChange={event => setService(event.target.value as DiagnosticService)}><option value="server">Сервер TEST</option><option value="emergency">Emergency</option><option value="inventory">Inventory</option><option value="tracker">Tracker</option></select></FormField>
          {service !== 'server' ? <><FormField id="diagnostic-route" label="Маршрут"><select value={route} onChange={event => setRoute(event.target.value as DiagnosticRequest['route'])}><option value="direct">Прямой с Mac</option><option value="server">Через сервер TEST</option></select></FormField><FormField error={validRobot ? undefined : 'Номер должен быть от 1000 до 3000.'} id="diagnostic-robot" label="Робот"><input inputMode="numeric" max="3000" min="1000" onChange={event => setRobot(event.target.value)} type="number" value={robot} /></FormField></> : null}
        </div>
        <div className="rp-device-actions"><Button busy={busy} disabled={snapshot?.running || (service !== 'server' && !validRobot)} onClick={() => void run()}>Запустить проверку</Button><Button disabled={busy} onClick={() => void refresh()} variant="secondary">Обновить снимок</Button></div>
        {message ? <Alert tone={message.includes('новой тестовой') ? 'info' : 'error'}>{message}</Alert> : null}
        {snapshot ? <div aria-live="polite" className="rp-device-diagnostics__snapshot">
          <p><strong>{snapshot.running ? 'Проверка выполняется…' : 'Проверка не выполняется'}</strong>{lastEvent ? ` · этап ${lastEvent.stage}` : ''}</p>
          {snapshot.results.length ? <ul aria-label="Результаты диагностики">{snapshot.results.map((result, index) => <li key={`${result.service}:${result.route}:${result.robot ?? ''}:${index}`}>{resultText(result)}</li>)}</ul> : <p>Результатов пока нет.</p>}
          {snapshot.events.length ? <details><summary>События ({snapshot.events.length})</summary><ol className="rp-device-diagnostics__events">{snapshot.events.map(event => <li key={event.id}><time>{event.time}</time> · {event.service} · {event.stage} · {statusLabels[event.status] ?? event.status}{event.code ? ` · ${event.code}` : ''}{event.elapsedMs === undefined ? '' : ` · ${event.elapsedMs} мс`}</li>)}</ol></details> : null}
        </div> : null}
      </div>
    </details>
  </Panel>
}
