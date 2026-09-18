import { useEffect, useState } from 'react'
import './SyncStatus.css'

type Props = {
  updatedAt: number | null
  isRevalidating?: boolean
  error?: unknown
}

export function SyncStatus({ updatedAt, isRevalidating = false, error }: Props) {
  const [online, setOnline] = useState(() => navigator.onLine !== false)
  useEffect(() => {
    const update = () => setOnline(navigator.onLine !== false)
    window.addEventListener('online', update)
    window.addEventListener('offline', update)
    return () => {
      window.removeEventListener('online', update)
      window.removeEventListener('offline', update)
    }
  }, [])
  const connection = !online ? 'Нет сети' : error ? 'Синхронизация задерживается' : isRevalidating ? 'Синхронизация…' : ''
  if (!connection) return null
  return <p className="rp-sync-status" data-warning={!online || Boolean(error)}
    title={updatedAt === null ? undefined : `Последняя успешная синхронизация: ${new Date(updatedAt).toLocaleString('ru-RU')}`}>
    {connection}
  </p>
}
