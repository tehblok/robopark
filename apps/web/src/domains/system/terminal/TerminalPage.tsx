import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, fetchWithTimeout } from '../../../api'
import { TerminalConnection } from './terminalTransport'
import { TerminalViewport } from './TerminalViewport'
import { terminalApi, type SessionOut, type TerminalApiClient, type TerminalCapabilities, type TerminalProfile } from './terminalApi'
import './terminal.css'

type OnlineUser = { id: number; username: string; role: string; access_status: string }
type ConnectionFactory = (session: SessionOut, callbacks: {
  onSession: (value: SessionOut) => void
  onState: (state: 'connecting' | SessionOut['state'], reason?: string) => void
  onEnded: (reason?: string) => void
  onAuthorizationFailure: () => void
}) => TerminalConnection

async function loadOnlineUser(): Promise<OnlineUser> {
  return fetchWithTimeout('/api/auth/me', { credentials: 'include', cache: 'no-store' }, 10_000, async response => {
    if (!response.ok) throw new ApiError(response.status, null)
    return await response.json() as OnlineUser
  })
}

function failureMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 401 && ['invalid_totp', 'invalid_password', 'invalid_credentials'].includes(String(error.detail))) {
    return 'Неверный пароль или код TOTP. Вход на сайт сохранён.'
  }
  if (error instanceof ApiError && error.status === 409 && error.detail === 'terminal_host_busy') return 'Хост занят обслуживанием. Завершите операцию и повторите попытку.'
  if (error instanceof ApiError && error.status === 403) return 'Доступ к терминалу отозван.'
  return 'Не удалось открыть терминал. Проверьте доступность хоста и повторите попытку.'
}

function formatDeadline(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
}

function SessionPanel({ session, apiClient, makeConnection, onChange, onRevoked }: {
  session: SessionOut
  apiClient: TerminalApiClient
  makeConnection: ConnectionFactory
  onChange: (value: SessionOut) => void
  onRevoked: () => void
}) {
  const [state, setState] = useState<'connecting' | SessionOut['state']>(session.state)
  const [reason, setReason] = useState<string | undefined>()
  const [connectionSession] = useState(session)
  const connection = useMemo(() => makeConnection(connectionSession, {
    onSession: value => { setState(value.state); onChange(value) },
    onState: (value, detail) => { setState(value); setReason(detail) },
    onEnded: value => {
      setState('ended'); setReason(value)
      onChange({ ...connectionSession, state: 'ended', termination_reason: value ?? null })
    },
    onAuthorizationFailure: onRevoked,
  }), [connectionSession, makeConnection, onChange, onRevoked])

  useEffect(() => {
    void connection.connect(connectionSession).catch(error => { setState('detached'); setReason(error instanceof Error ? error.message : 'connection_failed') })
    return () => connection.close()
  }, [connection, connectionSession])

  const terminate = async () => {
    try { onChange(await apiClient.terminate(session.id)) }
    finally { connection.close(); setState('ended') }
  }

  return <article className={`rp-terminal-session rp-terminal-session--${session.profile}`}>
    <header>
      <h2>{session.profile === 'root' ? 'Root-сессия' : 'Обычная сессия'}</h2>
      <p data-testid={`${session.profile}-deadline`}>{session.profile} · завершится в {formatDeadline(session.expires_at)}</p>
      {session.profile === 'root' && <p className="rp-terminal-danger">Root может изменить службы, обновление и весь хост. Таймер не отменяет уже сделанные изменения.</p>}
      {reason && <p role="status">Соединение: {reason}</p>}
    </header>
    {state !== 'ended'
      ? <TerminalViewport connection={connection} profile={session.profile} state={state} onTerminate={() => void terminate()} />
      : <p>Сессия завершена{session.termination_reason ? `: ${session.termination_reason}` : ''}.</p>}
  </article>
}

export function TerminalPage({
  apiClient = terminalApi,
  loadCurrentUser = loadOnlineUser,
  identityPollMs = 5_000,
  connectionFactory,
}: {
  apiClient?: TerminalApiClient
  loadCurrentUser?: () => Promise<OnlineUser>
  identityPollMs?: number
  connectionFactory?: ConnectionFactory
}) {
  const [user, setUser] = useState<OnlineUser | null>(null)
  const [capabilities, setCapabilities] = useState<TerminalCapabilities | null>(null)
  const [sessions, setSessions] = useState<SessionOut[]>([])
  const [profile, setProfile] = useState<TerminalProfile | null>(null)
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [authEnded, setAuthEnded] = useState(false)
  const identity = useRef<{ id: number; epoch: string } | null>(null)

  const revoke = useCallback(() => {
    setSessions([]); setUser(null); setCapabilities(null); setProfile(null); setPassword(''); setCode(''); setAuthEnded(true)
  }, [])
  const factory = useMemo<ConnectionFactory>(() => connectionFactory ?? ((session, callbacks) => new TerminalConnection({
    ticket: () => apiClient.attachTicket(session.id), onOutput: () => {},
    onSession: callbacks.onSession,
    onState: (state, reason) => {
      callbacks.onState(state, reason)
      if (state === 'ended') callbacks.onEnded(reason)
    },
    onAuthorizationFailure: callbacks.onAuthorizationFailure,
  })), [apiClient, connectionFactory])

  useEffect(() => {
    let active = true
    const verify = async (initial: boolean) => {
      try {
        const [nextUser, nextCapabilities] = await Promise.all([loadCurrentUser(), apiClient.capabilities()])
        if (!active) return
        if (nextUser.role !== 'royal' || nextUser.access_status !== 'approved') { revoke(); return }
        const epoch = nextCapabilities.broker_epoch ?? ''
        if (identity.current && (identity.current.id !== nextUser.id || identity.current.epoch !== epoch)) setSessions([])
        identity.current = { id: nextUser.id, epoch }
        setUser(nextUser); setCapabilities(nextCapabilities); setAuthEnded(false)
        if (initial) setSessions((await apiClient.list()).filter(value => value.state !== 'ended'))
      } catch (caught) {
        if (!active) return
        if (caught instanceof ApiError && (caught.status === 401 || caught.status === 403)) revoke()
        else if (initial) setError('Для терминала нужна свежая онлайн-проверка. Проверьте соединение и вернитесь на сайт.')
      }
    }
    void verify(true)
    const timer = window.setInterval(() => { if (!document.hidden) void verify(false) }, identityPollMs)
    return () => { active = false; window.clearInterval(timer) }
  }, [apiClient, identityPollMs, loadCurrentUser, revoke])

  useEffect(() => {
    const warning = (event: BeforeUnloadEvent) => {
      if (!sessions.some(value => value.state !== 'ended')) return
      event.preventDefault(); event.returnValue = ''
    }
    window.addEventListener('beforeunload', warning)
    return () => window.removeEventListener('beforeunload', warning)
  }, [sessions])

  useEffect(() => {
    const signedOut = (event: StorageEvent) => {
      if (event.key === 'robopark:local-signout:v1' && event.newValue) revoke()
    }
    window.addEventListener('storage', signedOut)
    return () => window.removeEventListener('storage', signedOut)
  }, [revoke])

  const updateSession = useCallback((value: SessionOut) => setSessions(current => current.map(item => item.id === value.id ? value : item)), [])
  const open = async () => {
    if (!profile || !capabilities?.available || !capabilities.capability_revision) return
    const sessionId = crypto.randomUUID()
    setBusy(true); setError(null)
    try {
      const grant = await apiClient.reauthorize({
        password, code, operation_kind: `terminal.open.${profile}`, operation_id: sessionId,
        capability_revision: capabilities.capability_revision,
      })
      const created = await apiClient.create({ id: sessionId, profile, capability_revision: capabilities.capability_revision }, grant.token)
      setSessions(current => [created, ...current.filter(item => item.id !== created.id)])
      setProfile(null)
    } catch (caught) { setError(failureMessage(caught)) }
    finally { setPassword(''); setCode(''); setBusy(false) }
  }

  if (authEnded) return <main className="rp-terminal-page"><h1>Терминал хоста</h1><p>Сессия входа завершена. Вернитесь на сайт и войдите снова.</p><a href="/login">Вернуться ко входу</a></main>
  if (!user) return <main className="rp-terminal-page"><h1>Терминал хоста</h1><p>{error ?? 'Проверяем онлайн-доступ…'}</p><a href="/system">Вернуться в систему</a></main>
  return <main className="rp-terminal-page">
    <header className="rp-terminal-page__header">
      <div><p className="rp-terminal-eyebrow">Royal · {user.username}</p><h1>Терминал хоста</h1></div>
      <a href="/system">Вернуться в систему</a>
    </header>
    {!capabilities?.available && <p role="alert">Терминал недоступен: {capabilities?.reason ?? 'нужно обновить компоненты хоста'}.</p>}
    {error && <p role="alert">{error}</p>}
    <div className="rp-terminal-actions">
      <button type="button" disabled={!capabilities?.available || sessions.some(value => value.profile === 'maintenance' && value.state !== 'ended')} onClick={() => setProfile('maintenance')}>Открыть терминал</button>
      <button type="button" className="rp-terminal-root-button" disabled={!capabilities?.available || sessions.some(value => value.profile === 'root' && value.state !== 'ended')} onClick={() => setProfile('root')}>Открыть root</button>
    </div>
    {profile && <div role="dialog" aria-modal="true" aria-label={profile === 'root' ? 'Подтверждение root' : 'Подтверждение терминала'} className="rp-terminal-auth">
      <h2>{profile === 'root' ? 'Отдельно подтвердите root' : 'Подтвердите открытие терминала'}</h2>
      {profile === 'root' && <p>Root имеет полный доступ к хосту. Потребуется свежий TOTP, отличный от использованного для обычной сессии.</p>}
      <label>Пароль<input type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} /></label>
      <label>Свежий код TOTP<input inputMode="numeric" autoComplete="one-time-code" value={code} onChange={event => setCode(event.target.value.replace(/\D/g, '').slice(0, 8))} /></label>
      <button type="button" disabled={busy || !password || code.length < 6} onClick={() => void open()}>Подтвердить и открыть</button>
      <button type="button" disabled={busy} onClick={() => { setProfile(null); setPassword(''); setCode('') }}>Отмена</button>
    </div>}
    <section className="rp-terminal-sessions" aria-label="Терминальные сессии">
      {sessions.map(session => <SessionPanel key={session.id} session={session} apiClient={apiClient} makeConnection={factory} onChange={updateSession} onRevoked={revoke} />)}
    </section>
  </main>
}
