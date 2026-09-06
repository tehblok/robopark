import { periodicDelay, resumeDelay, retryAfterMs } from '../../lib/pollingSchedule'
import { collaborationClient, type Handoff } from './collaborationClient'
import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../../api'
import { Button } from '../../design-system/actions/Button'

const empty: Handoff = { revision: 0, done: '', remaining: '', obstacles: '', author: null, updated_at: null }

function Content({ issueKey, owner, active, canWrite, onAuthorizationFailure }: {
  issueKey: string; owner: string; active: boolean; canWrite: boolean; onAuthorizationFailure?: (error: unknown) => void
}) {
  const [people, setPeople] = useState<string[]>([])
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState<Handoff>(empty)
  const [fresh, setFresh] = useState<Handoff | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [denied, setDenied] = useState(false)
  const deniedRef = useRef(false)
  const revision = useRef(0)
  const alive = useRef(true)
  const authFailure = useRef(onAuthorizationFailure)
  const storageKey = `robopark:handoff:v1:${JSON.stringify([owner, issueKey])}`
  const observeDenial = useCallback((caught: unknown) => {
    if (!(caught instanceof ApiError) || ![401, 403].includes(caught.status)) return false
    deniedRef.current = true
    setDenied(true)
    setPeople([])
    setValue(empty)
    setFresh(null)
    setLoaded(false)
    setOpen(false)
    setError('Доступ к совместной работе с задачей ограничен. Данные скрыты.')
    authFailure.current?.(caught)
    return true
  }, [])
  useEffect(() => { authFailure.current = onAuthorizationFailure }, [onAuthorizationFailure])
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  useEffect(() => {
    if (!active || denied) return
    let cancelled = false
    let inFlight = false
    let retryAt = 0
    let failures = 0
    let timer: ReturnType<typeof setTimeout> | undefined
    const visibleOnline = () => document.visibilityState !== 'hidden' && navigator.onLine !== false
    const schedule = (delay: number) => {
      clearTimeout(timer)
      if (!cancelled && visibleOnline()) timer = setTimeout(() => void ping(), delay)
    }
    const ping = async () => {
      if (cancelled || inFlight || !visibleOnline()) return
      inFlight = true
      let delay = periodicDelay(35_000)
      try {
        const result = await collaborationClient.presence(issueKey)
        if (!cancelled && !deniedRef.current && visibleOnline()) setPeople(result.people.map(person => person.username))
        failures = 0
        retryAt = 0
      } catch (caught) {
        if (!cancelled && observeDenial(caught)) {
          cancelled = true
          return
        }
        ++failures
        delay = periodicDelay(Math.max(Math.min(300_000, 35_000 * 2 ** Math.min(failures, 3)), retryAfterMs(caught)))
        retryAt = Date.now() + delay
      } finally {
        inFlight = false
        schedule(delay)
      }
    }
    const visibility = () => {
      clearTimeout(timer)
      if (visibleOnline()) schedule(Math.max(resumeDelay(), retryAt - Date.now()))
      else setPeople([])
    }
    schedule(resumeDelay())
    document.addEventListener('visibilitychange', visibility)
    window.addEventListener('online', visibility)
    window.addEventListener('offline', visibility)
    return () => {
      cancelled = true
      clearTimeout(timer)
      document.removeEventListener('visibilitychange', visibility)
      window.removeEventListener('online', visibility)
      window.removeEventListener('offline', visibility)
    }
  }, [active, issueKey, denied, observeDenial])

  useEffect(() => {
    if (!open || loaded || denied) return
    let cancelled = false
    void collaborationClient.handoff(issueKey).then(next => {
      if (cancelled || deniedRef.current) return
      let draft: Handoff | null = null
      try {
        const raw = localStorage.getItem(storageKey)
        const parsed = raw ? JSON.parse(raw) : null
        if (parsed && typeof parsed.done === 'string' && typeof parsed.remaining === 'string' && typeof parsed.obstacles === 'string' && Number.isInteger(parsed.revision)) draft = parsed
      } catch { setError('Локальный черновик недоступен. Проверьте хранилище браузера.') }
      setValue(draft ?? next)
      if (draft && draft.revision !== next.revision) setFresh(next)
      setLoaded(true)
    }).catch(caught => {
      if (cancelled || deniedRef.current) return
      if (observeDenial(caught)) return
      setError('Не удалось загрузить передачу смены.')
    })
    return () => { cancelled = true }
  }, [open, loaded, issueKey, storageKey, denied, observeDenial])

  const edit = (next: Handoff) => {
    if (deniedRef.current) return
    ++revision.current
    setValue(next)
    try { localStorage.setItem(storageKey, JSON.stringify(next)) }
    catch { setError('Не удалось сохранить черновик в браузере. Не закрывайте задачу до сохранения.') }
  }
  const save = async () => {
    if (busy || fresh || deniedRef.current) return
    setBusy(true)
    setError('')
    const captured = revision.current
    try {
      const saved = await collaborationClient.save(issueKey, value)
      if (!alive.current || deniedRef.current) return
      if (captured === revision.current) {
        setValue(saved)
        localStorage.removeItem(storageKey)
      } else {
        setValue(current => {
          const next = { ...current, revision: saved.revision, author: saved.author, updated_at: saved.updated_at }
          localStorage.setItem(storageKey, JSON.stringify(next))
          return next
        })
      }
    } catch (caught) {
      if (!alive.current || deniedRef.current) return
      if (observeDenial(caught)) return
      if (caught instanceof ApiError && caught.detail === 'tracker_handoff_conflict') {
        try {
          const current = await collaborationClient.handoff(issueKey)
          if (alive.current && !deniedRef.current) setFresh(current)
        } catch (refreshError) {
          if (alive.current && observeDenial(refreshError)) return
          // Keep the draft and original revision when a transient refresh fails.
        }
        setError('Передачу смены изменил коллега. Сравните версии; ваш текст сохранён.')
      } else setError('Не удалось сохранить передачу смены. Черновик сохранён; проверьте актуальную версию перед повтором.')
    } finally { if (alive.current) setBusy(false) }
  }

  if (denied) return <section className="issue-collaboration"><p role="alert">Доступ к совместной работе с задачей ограничен. Данные скрыты.</p></section>

  return <section className="issue-collaboration">
    {people.length > 0 && <p role="status">Сейчас в задаче: {people.join(', ')}. Это подсказка, задача доступна для работы.</p>}
    <details open={open} onToggle={event => setOpen(event.currentTarget.open)}>
      <summary>Передача смены</summary>
      {error && <p role="alert">{error}</p>}
      {!loaded ? <p>Загружаем передачу смены…</p> : <>
        {value.updated_at && <p className="issue-muted">{value.author ?? 'Удалённый пользователь'} · {new Date(value.updated_at).toLocaleString('ru-RU')}</p>}
        {(['done', 'remaining', 'obstacles'] as const).map((field, index) => <label key={field} className="issue-handoff-field">
          {['Сделано', 'Осталось', 'Препятствия'][index]}
          <textarea disabled={!canWrite} maxLength={4000} rows={2} value={value[field]} onChange={event => edit({ ...value, [field]: event.target.value })} />
        </label>)}
        {fresh && <div className="issue-update-notice">
          <p>Актуальная версия {fresh.revision}: {fresh.author} · {fresh.updated_at && new Date(fresh.updated_at).toLocaleString('ru-RU')}</p>
          <p>Сделано: {fresh.done || '—'}</p><p>Осталось: {fresh.remaining || '—'}</p><p>Препятствия: {fresh.obstacles || '—'}</p>
          <Button type="button" variant="secondary" onClick={() => { edit({ ...value, revision: fresh.revision }); setFresh(null); setError('') }}>Продолжить с моим текстом</Button>
        </div>}
        {canWrite && <Button type="button" disabled={busy || Boolean(fresh)} onClick={() => void save()}>{busy ? 'Сохраняем…' : 'Сохранить передачу смены'}</Button>}
      </>}
    </details>
  </section>
}

export function TaskCollaboration(props: React.ComponentProps<typeof Content>) {
  return <Content {...props} key={JSON.stringify([props.owner, props.issueKey])} />
}
