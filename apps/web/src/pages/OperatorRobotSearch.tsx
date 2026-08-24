import { Link } from 'react-router-dom'
import { TrackerWorkspace } from '../components/tracker/TrackerWorkspace'

export function OperatorRobotSearch() {
  return (
    <main className="page">
      <section className="workspace">
        <header>
          <h1>Operator robot search</h1>
          <Link to="/operator">Back</Link>
        </header>
        <TrackerWorkspace allowUntagged canWrite />
      </section>
    </main>
  )
}
