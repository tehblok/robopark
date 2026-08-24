import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type TrackerPolicySettings } from '../api'
import { TrackerWorkspace } from '../components/tracker/TrackerWorkspace'

export function AdminTrackerWorkspace() {
  const [policy, setPolicy] = useState<TrackerPolicySettings | null>(null)

  useEffect(() => {
    api.trackerPolicy().then(setPolicy).catch(() => setPolicy(null))
  }, [])

  const toggleMechanicWrite = async () => {
    if (!policy) return
    const next = await api.updateTrackerPolicy({ mechanic_can_write: !policy.mechanic_can_write })
    setPolicy(next)
  }

  return (
    <main className="page">
      <section className="workspace">
        <header>
          <h1>Admin tracker workspace</h1>
          <Link to="/admin">Back</Link>
        </header>
        {policy && (
          <div className="actions">
            <span>Mechanic write: {policy.mechanic_can_write ? 'on' : 'off'}</span>
            <button onClick={() => void toggleMechanicWrite()} type="button">Toggle mechanic write</button>
          </div>
        )}
        <TrackerWorkspace allowUntagged canWrite />
      </section>
    </main>
  )
}
