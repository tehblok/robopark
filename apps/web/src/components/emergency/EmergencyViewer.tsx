import { type FormEvent, useState } from 'react'
import { api, type EmergencySection, type EmergencySectionDetail } from '../../api'
import { Alert, EmptyState, PageShell, Panel } from '../PageShell'
import { mapApiError } from '../../i18n/errors'
import { ru } from '../../i18n/ru'

export function EmergencyViewer({ backTo }: { backTo: string }) {
  const [robotNumber, setRobotNumber] = useState('')
  const [vin, setVin] = useState('')
  const [sections, setSections] = useState<EmergencySection[]>([])
  const [detail, setDetail] = useState<EmergencySectionDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const resolveRobot = async () => {
    const query = robotNumber.trim()
    if (!query) return

    setLoading(true)
    setError('')
    setDetail(null)
    try {
      const data = await api.emergencyResolve(query)
      setVin(data.vin)
      setSections(data.sections)
    } catch (caught) {
      setVin('')
      setSections([])
      setError(mapApiError(caught, ru.errors.emergency))
    } finally {
      setLoading(false)
    }
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    await resolveRobot()
  }

  const openSection = async (sectionId: string) => {
    setLoading(true)
    setError('')
    try {
      setDetail(await api.emergencySection(vin, sectionId))
    } catch (caught) {
      setError(mapApiError(caught, ru.errors.emergencySection))
    } finally {
      setLoading(false)
    }
  }

  return (
    <PageShell backTo={backTo} subtitle={ru.emergency.subtitle} title={ru.emergency.title}>
      <Panel hint={ru.emergency.searchHint} title={ru.emergency.searchTitle}>
        <form className="inline-form" onSubmit={submit}>
          <input
            aria-label={ru.emergency.robotNumber}
            disabled={loading}
            onChange={(event) => setRobotNumber(event.target.value)}
            placeholder={ru.emergency.robotPlaceholder}
            required
            value={robotNumber}
          />
          <button disabled={loading} type="submit">{ru.emergency.resolve}</button>
          {vin && (
            <button
              className="btn btn-secondary"
              disabled={loading}
              onClick={resolveRobot}
              type="button"
            >
              {ru.emergency.refresh}
            </button>
          )}
        </form>
      </Panel>

      {error && <Alert tone="error">{error}</Alert>}

      {vin && (
        <Panel hint={ru.emergency.sectionsHint} title={`VIN: ${vin}`}>
          {sections.length ? (
            <div className="actions">
              {sections.map((section) => (
                <button
                  className="btn btn-filter"
                  disabled={loading}
                  key={section.id}
                  onClick={() => openSection(section.id)}
                  type="button"
                >
                  {section.title}
                </button>
              ))}
            </div>
          ) : (
            <EmptyState>{ru.emergency.sectionsEmpty}</EmptyState>
          )}
        </Panel>
      )}

      {detail && (
        <Panel title={detail.title}>
          {detail.fields.length ? detail.fields.map((field) => (
            <div className="detail-block" key={field.label}>
              <strong>{field.label}</strong>
              <pre>{field.lines.join('\n')}</pre>
            </div>
          )) : (
            <EmptyState>{ru.emergency.detailEmpty}</EmptyState>
          )}
        </Panel>
      )}
    </PageShell>
  )
}
