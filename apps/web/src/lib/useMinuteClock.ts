import { useEffect, useState } from 'react'

/** One clock per active screen; hidden screens neither tick nor request data. */
export function useMinuteClock(): number {
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    let timer = 0
    const schedule = () => {
      window.clearTimeout(timer)
      if (document.hidden) return
      timer = window.setTimeout(() => {
        setNow(Date.now())
        schedule()
      }, 60_000 - Date.now() % 60_000)
    }
    const resume = () => {
      if (!document.hidden) setNow(Date.now())
      schedule()
    }
    document.addEventListener('visibilitychange', resume)
    window.addEventListener('pageshow', resume)
    schedule()
    return () => {
      window.clearTimeout(timer)
      document.removeEventListener('visibilitychange', resume)
      window.removeEventListener('pageshow', resume)
    }
  }, [])
  return now
}
