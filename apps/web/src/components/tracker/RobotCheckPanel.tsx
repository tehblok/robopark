import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Alert } from '../PageShell'
import { Spinner } from '../ui/Feedback'
import { ru } from '../../i18n/ru'
import { inspectRobotHealth, robotCheckPathForRobot } from './robotHealth'

export function RobotCheckPanel({
  robot,
  inspect = inspectRobotHealth,
}: {
  robot?: string | null
  inspect?: (query: string) => Promise<string[]>
}) {
  const query = robot?.trim() ?? ''
  const [checking, setChecking] = useState(false)
  const [findings, setFindings] = useState<string[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const inspectRef = useRef(inspect)
  useEffect(() => { inspectRef.current = inspect }, [inspect])

  useEffect(() => {
    if (!query) {
      setFindings(null)
      setChecking(false)
      setFailed(false)
      return
    }
    let cancelled = false
    setChecking(true)
    setFindings(null)
    setFailed(false)
    void inspectRef
      .current(query)
      .then((next) => {
        if (!cancelled) setFindings(next)
      })
      .catch(() => {
        if (!cancelled) { setFindings(null); setFailed(true) }
      })
      .finally(() => {
        if (!cancelled) setChecking(false)
      })
    return () => {
      cancelled = true
    }
  }, [query, attempt])

  if (!query) return null

  return (
    <section className="robot-check">
      <div className="robot-check-head">
        <h3>{ru.tracker.robotCheck.title}</h3>
        <Link className="btn" to={robotCheckPathForRobot(query)}>
          {ru.tracker.robotCheck.open} {query}
        </Link>
      </div>
      {checking && (
        <p className="issue-muted">
          <Spinner label={ru.tracker.robotCheck.checking} />
        </p>
      )}
      {!checking && failed && (
        <Alert tone="error">
          <p>{ru.tracker.robotCheck.failed}</p>
          <button className="btn" onClick={() => setAttempt((value) => value + 1)}>
            {ru.tracker.robotCheck.retry}
          </button>
        </Alert>
      )}
      {!checking && findings && findings.length === 0 && (
        <Alert tone="success">{ru.tracker.robotCheck.noCritical}</Alert>
      )}
      {!checking && findings && findings.length > 0 && (
        <>
          <Alert tone="error">{ru.tracker.robotCheck.found}</Alert>
          <ul className="robot-check-findings">
            {findings.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
