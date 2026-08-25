import { type FormEvent, useState } from 'react'
import { api, type EmergencySection, type EmergencySectionDetail } from '../../api'
import { Alert, PageShell, Panel } from '../PageShell'
import { EmptyBlock, SkeletonList, Spinner } from '../ui/Feedback'
import { mapApiError } from '../../i18n/errors'
import { ru } from '../../i18n/ru'

export function EmergencyViewer() {
  const [robotNumber, setRobotNumber] = useState('')
  const [vin, setVin] = useState('')
  const [sections, setSections] = useState<EmergencySection[]>([])
  const [detail, setDetail] = useState<EmergencySectionDetail | null>(null)
  const [activeSection, setActiveSection] = useState('')
  const [loading, setLoading] = useState(false)
  const [sectionLoading, setSectionLoading] = useState(false)
  const [error, setError] = useState('')

  const resolveRobot = async () => {
    const query = robotNumber.trim()
    if (!query) return

    setLoading(true)
    setError('')
    setDetail(null)
    setActiveSection('')
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
    setSectionLoading(true)
    setActiveSection(sectionId)
    setError('')
    try {
      setDetail(await api.emergencySection(vin, sectionId))
    } catch (caught) {
      setDetail(null)
      setError(mapApiError(caught, ru.errors.emergencySection))
    } finally {
      setSectionLoading(false)
    }
  }

  return (
    <PageShell subtitle={ru.emergency.subtitle} title={ru.emergency.title}>
      <Panel hint={ru.emergency.searchHint} title={ru.emergency.searchTitle}>
        <form className="search-form" onSubmit={submit}>
          <input
            aria-label={ru.emergency.robotNumber}
            disabled={loading}
            onChange={(event) => setRobotNumber(event.target.value)}
            placeholder={ru.emergency.robotPlaceholder}
            required
            value={robotNumber}
          />
          <button className="btn" disabled={loading || !robotNumber.trim()} type="submit">
            {loading ? <Spinner label="Поиск" /> : ru.emergency.resolve}
          </button>
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

      {loading && <SkeletonList rows={2} />}

      {!loading && !vin && !error && (
        <EmptyBlock
          hint="Номер робота преобразуется в VIN, затем доступны разделы Emergency."
          icon="⚑"
          title="Введите номер робота"
        />
      )}

      {!loading && vin && (
        <Panel hint={ru.emergency.sectionsHint} title={`VIN: ${vin}`}>
          {sections.length ? (
            <div className="task-filters">
              {sections.map((section) => (
                <button
                  className={`btn btn-filter${activeSection === section.id ? ' is-active' : ''}`}
                  disabled={sectionLoading}
                  key={section.id}
                  onClick={() => openSection(section.id)}
                  type="button"
                >
                  {section.title}
                </button>
              ))}
            </div>
          ) : (
            <EmptyBlock icon="📭" title={ru.emergency.sectionsEmpty} />
          )}
        </Panel>
      )}

      {sectionLoading && <SkeletonList rows={2} />}

      {!sectionLoading && detail && (
        <Panel title={detail.title}>
          {detail.fields.length ? (
            detail.fields.map((field) => (
              <div className="detail-block" key={field.label}>
                <strong>{field.label}</strong>
                <pre>{field.lines.join('\n')}</pre>
              </div>
            ))
          ) : (
            <EmptyBlock icon="📭" title={ru.emergency.detailEmpty} />
          )}
        </Panel>
      )}
    </PageShell>
  )
}
