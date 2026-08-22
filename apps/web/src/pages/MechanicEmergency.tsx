import { type FormEvent, useState } from 'react'
import { api, type EmergencySection, type EmergencySectionDetail } from '../api'
import { Alert, EmptyState, PageShell, Panel } from '../components/PageShell'
import { mapApiError } from '../i18n/errors'
import { ru } from '../i18n/ru'

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
    } catch (caught) {
      setError(mapApiError(caught, ru.errors.emergency))
    }
  }

  const openSection = async (sectionId: string) => {
    setError('')
    try {
      setDetail(await api.mechanicEmergencySection(vin, sectionId))
    } catch (caught) {
      setError(mapApiError(caught, ru.errors.emergencySection))
    }
  }

  return (
    <PageShell
      backTo="/mechanic"
      subtitle="Номер робота → VIN → разделы данных Emergency API."
      title="Emergency"
    >
      <Panel hint="Нужен cookie Emergency в настройках администратора." title="Поиск робота">
        <form className="inline-form" onSubmit={resolve}>
          <input
            aria-label="Номер робота"
            onChange={(event) => setRobotNumber(event.target.value)}
            placeholder="447"
            required
            value={robotNumber}
          />
          <button type="submit">Проверить</button>
        </form>
      </Panel>

      {error && <Alert tone="error">{error}</Alert>}

      {vin && (
        <Panel hint="Выберите раздел для просмотра полей." title={`VIN: ${vin}`}>
          {sections.length ? (
            <div className="actions">
              {sections.map((section) => (
                <button
                  className="btn btn-filter"
                  key={section.id}
                  onClick={() => openSection(section.id)}
                  type="button"
                >
                  {section.title}
                </button>
              ))}
            </div>
          ) : (
            <EmptyState>Разделы не найдены.</EmptyState>
          )}
        </Panel>
      )}

      {detail && (
        <Panel title={detail.title}>
          {detail.fields.map((field) => (
            <div className="detail-block" key={field.label}>
              <strong>{field.label}</strong>
              <pre>{field.lines.join('\n')}</pre>
            </div>
          ))}
        </Panel>
      )}
    </PageShell>
  )
}
