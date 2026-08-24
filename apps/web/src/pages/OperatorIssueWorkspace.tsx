import { Link } from 'react-router-dom'
import { TrackerWorkspace } from '../components/tracker/TrackerWorkspace'
import { useParkContext } from '../park-context'

export function OperatorIssueWorkspace() {
  const { parkId, parks } = useParkContext()
  const selected = parks.find((park) => park.id === parkId)
  const defaultQueue = (selected?.tracker_queue || 'SDCFLEETOPS').trim() || 'SDCFLEETOPS'
  const defaultPark = selected?.tag?.trim() || undefined

  return (
    <main className="page">
      <section className="workspace">
        <header>
          <h1>Тикеты Startrek</h1>
          <Link to="/operator">Назад</Link>
        </header>
        <TrackerWorkspace
          allowUntagged
          canWrite
          defaultPark={defaultPark}
          defaultQueue={defaultQueue}
        />
      </section>
    </main>
  )
}
