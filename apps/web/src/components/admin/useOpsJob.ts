import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, type OpsJob } from '../../api'
import { coalesceLoader } from '../../lib/resource'

export const activeJob = (job: OpsJob | null) => job?.state === 'queued' || job?.state === 'running'

export function useOpsJob() {
  const [job, setJob] = useState<OpsJob | null>(null)
  const [error, setError] = useState(false)
  const generation = useRef(0)
  const mounted = useRef(false)
  const accept = useCallback((next: OpsJob) => { generation.current++; setJob(next); setError(false) }, [])
  const load = useCallback(async () => {
    const request = generation.current
    try {
      const next = await coalesceLoader('ops:job-request', api.opsJob)
      if (mounted.current && request === generation.current) { setJob(next); setError(false) }
    } catch (caught) {
      if (mounted.current && request === generation.current) {
        if (caught instanceof ApiError && caught.status === 404) setJob(null)
        else { setError(true); throw caught }
      }
    }
  }, [])
  useEffect(() => {
    mounted.current = true
    void load().catch(() => undefined)
    const invalidate = () => { mounted.current = false; generation.current++ }
    return invalidate
  }, [load])
  const active = activeJob(job)
  useEffect(() => {
    let current = true
    let pending = false
    let failures = 0
    let timer: number | undefined
    const clear = () => window.clearTimeout(timer)
    const schedule = () => {
      clear()
      if (current && active && !document.hidden) timer = window.setTimeout(() => void poll(), Math.min(30000, 2500 * 2 ** failures))
    }
    const poll = async () => {
      if (!current || pending || document.hidden) return
      pending = true
      clear()
      try { await load(); failures = 0 } catch { failures++ }
      finally { pending = false; schedule() }
    }
    const focus = () => { clear(); if (!document.hidden) void poll() }
    window.addEventListener('focus', focus)
    document.addEventListener('visibilitychange', focus)
    schedule()
    return () => { current = false; clear(); window.removeEventListener('focus', focus); document.removeEventListener('visibilitychange', focus) }
  }, [active, job?.id, load])
  return { job, accept, error, refresh: load }
}
