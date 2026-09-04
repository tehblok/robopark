import { useCallback, useLayoutEffect, useRef, useState } from 'react'
import { pollDelayAfterFailure } from './polling'

// A stable task callback is the request identity. A changed callback starts a new
// generation; old requests cannot coalesce with it or schedule its next timeout.
export function useVisibilityPolling({ enabled, online, task }: { enabled: boolean; online: boolean; task: () => Promise<void> }) {
  const [pending, setPending] = useState(false)
  const runner = useRef<(manual: boolean) => Promise<void>>(async () => undefined)
  const connection = useRef(online)
  useLayoutEffect(() => { connection.current = online }, [online])
  useLayoutEffect(() => {
    let current = true
    let failures = 0
    let timer: number | undefined
    let inFlight: Promise<void> | null = null
    const clear = () => { window.clearTimeout(timer); timer = undefined }
    const automatic = () => current && enabled && connection.current && !document.hidden
    const schedule = (delay: number) => { clear(); if (automatic()) timer = window.setTimeout(() => void run(false), delay) }
    const run = (manual: boolean): Promise<void> => {
      if (!current || !enabled || (!manual && !automatic())) return Promise.resolve()
      if (inFlight) return inFlight
      clear(); setPending(true)
      inFlight = Promise.resolve().then(() => {
        if (current && enabled) return task()
      }).then(() => {
        if (!current) return
        failures = 0; schedule(2500)
      }, () => {
        if (!current) return
        schedule(pollDelayAfterFailure(failures)); failures += 1
      }).finally(() => {
        if (!current) return
        inFlight = null; setPending(false)
      })
      return inFlight
    }
    const visibility = () => { clear(); if (automatic()) void run(false) }
    runner.current = run
    setPending(false)
    document.addEventListener('visibilitychange', visibility)
    void run(false)
    return () => { current = false; clear(); document.removeEventListener('visibilitychange', visibility) }
  }, [enabled, task])
  useLayoutEffect(() => { void runner.current(false) }, [online])
  return { pending, refreshNow: useCallback(() => runner.current(true), []) }
}
