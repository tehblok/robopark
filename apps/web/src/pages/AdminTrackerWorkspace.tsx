import { useEffect, useState } from 'react'
import { api, type TrackerPolicySettings } from '../api'
import { PageShell, Panel } from '../components/PageShell'
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
    <PageShell backTo="/admin" title="Рабочий стол Tracker">
      {policy && (
        <Panel hint="Политика записи механика в Tracker." title="Политика">
          <div className="actions">
            <span>Запись механика: {policy.mechanic_can_write ? 'вкл' : 'выкл'}</span>
            <button onClick={() => void toggleMechanicWrite()} type="button">
              Переключить
            </button>
          </div>
        </Panel>
      )}
      <TrackerWorkspace allowUntagged canWrite />
    </PageShell>
  )
}
