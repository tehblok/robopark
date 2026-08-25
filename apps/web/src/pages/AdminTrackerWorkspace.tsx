import { useEffect, useState } from 'react'
import { api, type TrackerPolicySettings } from '../api'
import { PageShell, Panel } from '../components/PageShell'
import { TrackerWorkspace } from '../components/tracker/TrackerWorkspace'
import { useParkContext } from '../park-context'

export function AdminTrackerWorkspace() {
  const { parkId, parks } = useParkContext()
  const [policy, setPolicy] = useState<TrackerPolicySettings | null>(null)
  const selected = parks.find((park) => park.id === parkId)
  const defaultQueue = (selected?.tracker_queue || 'SDCFLEETOPS').trim() || 'SDCFLEETOPS'
  const defaultPark = selected?.tag?.trim() || undefined

  useEffect(() => {
    api.trackerPolicy().then(setPolicy).catch(() => setPolicy(null))
  }, [])

  const toggleMechanicWrite = async () => {
    if (!policy) return
    const next = await api.updateTrackerPolicy({ mechanic_can_write: !policy.mechanic_can_write })
    setPolicy(next)
  }

  return (
    <PageShell backTo="/admin" title="Рабочий стол Startrek">
      {policy && (
        <Panel hint="Политика записи механика во внутренний Tracker (st.yandex-team.ru)." title="Политика">
          <div className="actions">
            <span>Запись механика: {policy.mechanic_can_write ? 'вкл' : 'выкл'}</span>
            <button onClick={() => void toggleMechanicWrite()} type="button">
              Переключить
            </button>
          </div>
        </Panel>
      )}
      <TrackerWorkspace
        allowUntagged
        canWrite
        defaultPark={defaultPark}
        defaultQueue={defaultQueue}
      />
    </PageShell>
  )
}
