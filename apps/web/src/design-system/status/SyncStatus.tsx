import { useEffect, useState } from 'react'
import './SyncStatus.css'

type Props = {
  updatedAt: number | null
  isRevalidating?: boolean
  error?: unknown
}

export function SyncStatus({ updatedAt, isRevalidating = false, error }: Props) {
  const [clock, setClock] = useState(() => ({ now: Date.now(), online: navigator.onLine !== false }))
  useEffect(() => {
    const update = () => setClock({ now: Date.now(), online: navigator.onLine !== false })
    const timer = window.setInterval(() => { if (!document.hidden) update() }, 30_000)
    window.addEventListener('online', update)
    window.addEventListener('offline', update)
    document.addEventListener('visibilitychange', update)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('online', update)
      window.removeEventListener('offline', update)
      document.removeEventListener('visibilitychange', update)
    }
  }, [])
  const age = updatedAt === null ? null : Math.max(0, clock.now - updatedAt)
  const relative = age === null ? '' : age < 60_000 ? 'только что' : age < 3_600_000
    ? `${Math.floor(age / 60_000)} мин. назад` : age < 86_400_000
      ? `${Math.floor(age / 3_600_000)} ч. назад` : `${Math.floor(age / 86_400_000)} дн. назад`
  const connection = !clock.online ? 'Нет сети' : error ? 'Синхронизация задерживается' : isRevalidating ? 'Синхронизация…' : ''
  return <p className="rp-sync-status" data-warning={!clock.online || Boolean(error)}
    title={updatedAt === null ? undefined : `Последняя успешная синхронизация: ${new Date(updatedAt).toLocaleString('ru-RU')}`}>
    {connection ? <span>{connection}</span> : null}
    <span>{updatedAt === null ? 'Данные ещё не синхронизированы' : `Синхронизировано ${relative}`}</span>
  </p>
}
