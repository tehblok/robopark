import { type FormEvent, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type EmergencySection, type EmergencySectionDetail } from '../api'

export function MechanicEmergency() {
  const [robotNumber, setRobotNumber] = useState('')
  const [vin, setVin] = useState('')
  const [sections, setSections] = useState<EmergencySection[]>([])
  const [detail, setDetail] = useState<EmergencySectionDetail | null>(null)
  const [error, setError] = useState('')

  const resolve = async (event: FormEvent) => {
    event.preventDefault()
    setError('')
    setDetail(null)
    try {
      const data = await api.mechanicEmergencyResolve(robotNumber.trim())
      setVin(data.vin)
      setSections(data.sections)
    } catch {
      setError('Emergency lookup failed. Check cookie configuration.')
    }
  }

  const openSection = async (sectionId: string) => {
    setError('')
    try {
      setDetail(await api.mechanicEmergencySection(vin, sectionId))
    } catch {
      setError('Could not load section.')
    }
  }

  return (
    <main className="page">
      <section className="workspace">
        <header>
          <h1>Emergency</h1>
          <Link to="/mechanic">Back</Link>
        </header>
        <form className="inline-form" onSubmit={resolve}>
          <input
            aria-label="Robot number"
            onChange={(event) => setRobotNumber(event.target.value)}
            placeholder="447"
            required
            value={robotNumber}
          />
          <button type="submit">Resolve</button>
        </form>
        {error && <p className="error">{error}</p>}
        {vin && (
          <>
            <p>VIN: {vin}</p>
            <ul>
              {sections.map((section) => (
                <li key={section.id}>
                  <button onClick={() => openSection(section.id)} type="button">
                    {section.title}
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
        {detail && (
          <article>
            <h2>{detail.title}</h2>
            {detail.fields.map((field) => (
              <div key={field.label}>
                <strong>{field.label}</strong>
                <pre>{field.lines.join('\n')}</pre>
              </div>
            ))}
          </article>
        )}
      </section>
    </main>
  )
}
