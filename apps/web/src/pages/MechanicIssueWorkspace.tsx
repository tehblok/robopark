import { Link } from 'react-router-dom'
import { TrackerWorkspace } from '../components/tracker/TrackerWorkspace'

export function MechanicIssueWorkspace() {
  return (
    <main className="page">
      <section className="workspace">
        <header>
          <h1>Mechanic tracker workspace</h1>
          <Link to="/mechanic">Back</Link>
        </header>
        <TrackerWorkspace allowUntagged={false} canWrite />
      </section>
    </main>
  )
}
