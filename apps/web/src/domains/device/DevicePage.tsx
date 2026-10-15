import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, PageShell, Panel } from '../../components/PageShell'
import { Button } from '../../design-system/actions/Button'
import { LoadingState } from '../../design-system/feedback/AsyncState'
import { desktopRuntime } from '../../pwa/desktopRuntime'
import { deviceClient, type DesktopAgentAction, type DesktopAgentStatus, type DeviceClient } from './deviceClient'
import './device.css'

const serverLabels: Record<DesktopAgentStatus['server']['state'], string> = {
  connected: 'Сервер на связи', connecting: 'Подключаемся к серверу…',
  offline: 'Сервер недоступен', error: 'Ошибка связи с сервером',
}
const syncLabels: Record<NonNullable<DesktopAgentStatus['sync']>['state'], string> = {
  syncing: 'Идёт синхронизация…', pending: 'Есть данные для отправки',
  offline: 'Без сети: данные сохранены на Mac', error: 'Синхронизации нужно внимание',
}

function CorporateState({ corporate }: { corporate: DesktopAgentStatus['corporate'] }) {
  if (corporate.state === 'ready') return <Alert tone="success">Корпоративные доступы готовы.</Alert>
  if (corporate.state === 'checking') return <Alert tone="info">Ожидаем завершения входа…</Alert>
  if (corporate.state === 'denied') return <Alert tone="error">{corporate.message ?? 'Вход выполнен, но нужные корпоративные доступы не выданы.'}</Alert>
  if (corporate.state === 'unavailable') return <Alert tone="warning">{corporate.message ?? 'Не удалось проверить корпоративные доступы.'}</Alert>
  return <Alert tone="info">Вход в корпоративные системы не выполнен</Alert>
}

export function DevicePage({
  client = deviceClient,
  desktop = desktopRuntime() !== null,
}: { client?: DeviceClient; desktop?: boolean }) {
  const [status, setStatus] = useState<DesktopAgentStatus | null>(null)
  const [failed, setFailed] = useState(false)
  const [busy, setBusy] = useState<DesktopAgentAction['action'] | null>(null)
  const mounted = useRef(false)
  const actionGeneration = useRef(0)
  const refreshInFlight = useRef<Promise<void> | null>(null)
  const refresh = useCallback(async () => {
    if (refreshInFlight.current) return refreshInFlight.current
    const generation = actionGeneration.current
    const task = (async () => {
      try {
        const next = await client.status()
        if (mounted.current && generation === actionGeneration.current) { setStatus(next); setFailed(false) }
      } catch {
        if (mounted.current && generation === actionGeneration.current) setFailed(true)
      }
    })()
    refreshInFlight.current = task
    try { await task } finally { if (refreshInFlight.current === task) refreshInFlight.current = null }
  }, [client])
  useEffect(() => {
    mounted.current = true
    if (desktop) void refresh()
    return () => { mounted.current = false; actionGeneration.current += 1 }
  }, [desktop, refresh])
  const shouldPoll = status?.corporate.state === 'checking'
    || status?.account.state === 'checking'
    || status?.server.state === 'connecting'
    || status?.update.state === 'checking'
    || status?.legacyWorkspaceAvailable === null
  useEffect(() => {
    if (!desktop || busy) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const interval = shouldPoll ? 2_500 : 15_000
    const poll = async () => {
      if (document.visibilityState !== 'hidden') await refresh()
      if (!cancelled) timer = setTimeout(() => { void poll() }, interval)
    }
    const visible = () => { if (document.visibilityState !== 'hidden') void refresh() }
    document.addEventListener('visibilitychange', visible)
    timer = setTimeout(() => { void poll() }, interval)
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
      document.removeEventListener('visibilitychange', visible)
    }
  }, [desktop, refresh, shouldPoll, busy])
  const act = async (payload: DesktopAgentAction) => {
    const generation = ++actionGeneration.current
    setBusy(payload.action)
    try {
      const next = await client.action(payload)
      if (mounted.current && generation === actionGeneration.current) { setStatus(next); setFailed(false) }
    } catch {
      if (mounted.current && generation === actionGeneration.current) setFailed(true)
    } finally {
      if (mounted.current && generation === actionGeneration.current) setBusy(null)
    }
  }

  if (!desktop) return <PageShell title="Этот Mac"><Alert tone="info">Этот раздел доступен в приложении Robopark на Mac.</Alert></PageShell>
  return <PageShell title="Этот Mac" subtitle="Связь с основным сервером, корпоративные доступы и резервная работа этого Mac.">
    {failed && <Alert tone="error">Локальный сервис Robopark не отвечает. Повторите проверку.</Alert>}
    {!status ? failed
      ? <Button onClick={() => void refresh()} variant="secondary">Повторить</Button>
      : <LoadingState label="Проверяем этот Mac" variant="page" />
      : <div className="rp-device-page">
        <Panel title="Аккаунт">
          <p><strong>{status.account.label || 'Вход не выполнен'}{status.account.role ? ` · ${status.account.role}` : ''}</strong></p>
          <p>{status.account.registered ? 'Этот Mac закреплён за аккаунтом.' : 'Этот Mac ещё не закреплён за аккаунтом.'}</p>
        </Panel>
        <Panel title="Связь и синхронизация">
          <p><strong>{serverLabels[status.server.state]}</strong></p>
          {status.sync ? <><p>{syncLabels[status.sync.state]} · в очереди {status.sync.pending} · конфликтов {status.sync.conflicts}</p>
          {status.sync.message && <p>{status.sync.message}</p>}</> : <p>Очередь офлайн-данных показана в центре синхронизации.</p>}
          {status.server.state !== 'connected' && <Button busy={busy === 'connect'} onClick={() => void act({ action: 'connect' })} variant="secondary">Восстановить связь</Button>}
        </Panel>
        <Panel title="Корпоративные доступы" hint="Пароли вводятся в защищённом окне и не попадают в веб-интерфейс.">
          <CorporateState corporate={status.corporate} />
          <dl className="rp-device-access"><div><dt>Tracker</dt><dd>{status.corporate.tracker === 'ready' ? 'готов' : 'нужна проверка'}</dd></div><div><dt>Inventory</dt><dd>{status.corporate.inventory === 'ready' ? 'готов' : 'нужна проверка'}</dd></div><div><dt>Emergency</dt><dd>{status.corporate.emergency === 'ready' ? 'готов' : 'нужна проверка'}</dd></div></dl>
          {status.server.state !== 'connected' && <p>Для входа сначала восстановите связь с сервером.</p>}
          <div className="rp-device-actions">
            <Button busy={busy === 'corporateLogin'} disabled={!status.corporate.canLogin || status.corporate.state === 'checking' || status.server.state !== 'connected'} onClick={() => void act({ action: 'corporateLogin' })}>Войти в корпоративные системы</Button>
            <Button busy={busy === 'reloadKeychain'} onClick={() => void act({ action: 'reloadKeychain' })} variant="secondary">Повторно проверить доступы</Button>
          </div>
        </Panel>
        <Panel title="Фоновая работа">
          <p>Основную обработку выполняет сервер. Этот Mac подключается как резерв при сбоях и для задач, которым нужны его локальные доступы или ресурсы.</p>
          <div className="rp-device-actions">
            <Button busy={busy === 'autostart'} disabled={!status.autostart.supported} onClick={() => void act({ action: 'autostart', enabled: !status.autostart.enabled })} variant="secondary">{status.autostart.enabled ? 'Выключить автозапуск' : 'Включить автозапуск'}</Button>
            <Button busy={busy === 'pause'} onClick={() => void act({ action: 'pause', enabled: !status.work.paused })} variant="secondary">{status.work.paused ? 'Возобновить работу' : 'Приостановить работу'}</Button>
          </div>
        </Panel>
        {status.legacyWorkspaceAvailable === true && <Panel title="Черновики предыдущей версии">
          <Alert tone="warning">На этом Mac найдены офлайн-данные из встроенного окна Robopark RC5. Они не переносятся в системный браузер автоматически.</Alert>
          <p>Откройте прежнее рабочее окно, дождитесь синхронизации черновиков и только после этого продолжайте работу в браузере.</p>
          <Button busy={busy === 'openLegacyWorkspace'} onClick={() => void act({ action: 'openLegacyWorkspace' })} variant="secondary">Открыть прежнее рабочее окно</Button>
        </Panel>}
        <Panel title="Обновление">
          <p>Установлена версия {status.update.currentVersion}{status.update.availableVersion ? ` · доступна ${status.update.availableVersion}` : ''}</p>
          {status.update.message && <p>{status.update.message}</p>}
          <Button busy={busy === 'checkUpdate'} onClick={() => void act({ action: 'checkUpdate' })} variant="secondary">Проверить обновление</Button>
        </Panel>
      </div>}
  </PageShell>
}
