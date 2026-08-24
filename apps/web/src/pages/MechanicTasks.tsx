import { Link } from 'react-router-dom'
import { TrackerWorkspace } from '../components/tracker/TrackerWorkspace'

export function MechanicTasks() {
  return (
    <main className="page">
      <section className="workspace">
        <header>
          <h1>Mechanic tasks</h1>
          <Link to="/mechanic">Back</Link>
        </header>
        <TrackerWorkspace allowUntagged={false} canWrite />
      </section>
    </main>
  )
}
