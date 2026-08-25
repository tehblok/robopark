import { useEffect, useState } from 'react'
import { api, type TrackerPolicySettings } from '../api'
import { Alert, PageShell, Panel } from '../components/PageShell'
import { Toggle } from '../components/ui/Tabs'
import { TrackerWorkspace } from '../components/tracker/TrackerWorkspace'
import { useParkContext } from '../park-context'

export function AdminTrackerWorkspace() {
  const { parkId, parks } = useParkContext()
  const [policy, setPolicy] = useState<TrackerPolicySettings | null>(null)
  const [policyError, setPolicyError] = useState('')
  const [policyBusy, setPolicyBusy] = useState(false)
  const selected = parks.find((park) => park.id === parkId)
  const defaultQueue = (selected?.tracker_queue || 'SDCFLEETOPS').trim() || 'SDCFLEETOPS'
  const defaultPark = selected?.tag?.trim() || undefined

  useEffect(() => {
    api
      .trackerPolicy()
      .then(setPolicy)
      .catch(() => {
        setPolicy(null)
        setPolicyError('Не удалось загрузить политику Tracker.')
      })
  }, [])

  const toggleMechanicWrite = async (next: boolean) => {
    if (!policy || policyBusy) return
    setPolicyBusy(true)
    setPolicyError('')
    try {
      const updated = await api.updateTrackerPolicy({ mechanic_can_write: next })
      setPolicy(updated)
    } catch {
      setPolicyError('Не удалось обновить политику.')
    } finally {
      setPolicyBusy(false)
    }
  }

  return (
    <PageShell backTo="/admin" title="Рабочий стол Startrek">
      {policyError && <Alert tone="error">{policyError}</Alert>}

      {policy && (
        <Panel
          hint="Политика записи механика во внутренний Tracker (st.yandex-team.ru)."
          title="Политика"
        >
          <div className="toggle-list">
            <Toggle
              checked={policy.mechanic_can_write}
              disabled={policyBusy}
              label={policyBusy ? 'Сохранение…' : 'Запись механика'}
              onChange={(next) => void toggleMechanicWrite(next)}
            />
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
