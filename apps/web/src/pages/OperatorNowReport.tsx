import { Link } from 'react-router-dom'
import { TrackerWorkspace } from '../components/tracker/TrackerWorkspace'

export function OperatorNowReport() {
  return (
    <main className="page">
      <section className="workspace">
        <header>
          <h1>Operator now report</h1>
          <Link to="/operator">Back</Link>
        </header>
        <TrackerWorkspace allowUntagged canWrite />
      </section>
    </main>
  )
}
