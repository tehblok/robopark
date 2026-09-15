import { useCallback, useLayoutEffect, useRef, useState } from 'react'
import { pollDelayAfterFailure, ROBOT_POLL_MS } from './polling'
import { periodicDelay, retryAfterMs } from '../../lib/pollingSchedule'

// A stable task callback is the request identity. A changed callback starts a new
// generation; old requests cannot coalesce with it or schedule its next timeout.
export function useVisibilityPolling({ enabled, online, task }: { enabled: boolean; online: boolean; task: (force?: boolean) => Promise<void> }) {
  const [pending, setPending] = useState(false)
  const runner = useRef<(manual: boolean) => Promise<void>>(async () => undefined)
  const resumeRunner = useRef<() => void>(() => {})
  const connection = useRef(online)
  useLayoutEffect(() => { connection.current = online }, [online])
  useLayoutEffect(() => {
    let current = true
    let initialPoll = true
    let failures = 0
    let retryAt = 0
    let timer: number | undefined
    let inFlight: Promise<void> | null = null
    const clear = () => { window.clearTimeout(timer); timer = undefined }
    const automatic = () => current && enabled && connection.current && !document.hidden
    const schedule = (delay: number) => { clear(); if (automatic()) timer = window.setTimeout(() => void run(false), delay) }
    const run = (manual: boolean): Promise<void> => {
      if (!current || !enabled || (!manual && !automatic())) return Promise.resolve()
      if (!manual && Date.now() < retryAt) { schedule(retryAt - Date.now()); return Promise.resolve() }
      if (inFlight) return inFlight
      clear(); setPending(true)
      inFlight = Promise.resolve().then(() => {
        if (current && enabled && (manual || automatic())) return task(manual)
      }).then(() => {
        if (!current) return
        failures = 0; retryAt = 0; schedule(periodicDelay(ROBOT_POLL_MS, initialPoll)); initialPoll = false
      }, (error: unknown) => {
        if (!current) return
        const delay = periodicDelay(Math.max(pollDelayAfterFailure(failures), retryAfterMs(error)))
        retryAt = Date.now() + delay
        schedule(delay); failures += 1
      }).finally(() => {
        if (!current) return
        inFlight = null; setPending(false)
      })
      return inFlight
    }
    const visibility = () => {
      clear()
      if (!automatic()) return
      retryAt = 0
      void run(false)
    }
    resumeRunner.current = visibility
    runner.current = run
    setPending(false)
    document.addEventListener('visibilitychange', visibility)
    void run(false)
    return () => { current = false; clear(); document.removeEventListener('visibilitychange', visibility) }
  }, [enabled, task])
  useLayoutEffect(() => { resumeRunner.current() }, [online])
  return { pending, refreshNow: useCallback(() => runner.current(true), []) }
}
