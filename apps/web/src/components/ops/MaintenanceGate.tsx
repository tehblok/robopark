import { useEffect, useRef, useState } from 'react'
import { api, type OpsMaintenance } from '../../api'
import { MaintenanceOverlay } from './MaintenanceOverlay'

const POLL_MS = 2000

export function MaintenanceGate() {
  const [status, setStatus] = useState<OpsMaintenance | null>(null)
  const lastActive = useRef<OpsMaintenance | null>(null)

  useEffect(() => {
    let cancelled = false
    const tick = async () => {
      try {
        const next = await api.opsMaintenance()
        if (cancelled) return
        if (next.active) lastActive.current = next
        else lastActive.current = null
        setStatus(next)
      } catch {
        if (cancelled) return
        // Keep showing maintenance if we last saw it active — transient API errors
        // should not open the cabinet mid-job.
        if (lastActive.current?.active) setStatus(lastActive.current)
      }
    }
    void tick()
    const id = window.setInterval(() => void tick(), POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [])

  return <MaintenanceOverlay status={status} />
}
