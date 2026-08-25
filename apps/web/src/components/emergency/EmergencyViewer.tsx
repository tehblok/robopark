import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import {
  ApiError,
  api,
  type EmergencySection,
  type EmergencySectionDetail,
  type EmergencySnapshot,
} from '../../api'
import { Alert, PageShell, Panel } from '../PageShell'
import { EmptyBlock, SkeletonList, Spinner } from '../ui/Feedback'
import { mapApiError } from '../../i18n/errors'
import { ru } from '../../i18n/ru'
import { CookieStaleStub } from './CookieStaleStub'
import { InspectionMap } from './InspectionMap'
import { RobotSchematic } from './RobotSchematic'

const SNAPSHOT_POLL_MS = 2500

function isCookieInvalid(error: unknown): boolean {
  return error instanceof ApiError && error.detail === 'emergency_cookie_invalid'
}

function wheelsSectionId(sections: EmergencySection[]): string {
  const match = sections.find(
    (section) =>
      section.id === 'wheels' || /колес|wheel/i.test(section.title),
  )
  return match?.id ?? 'wheels'
}

export function EmergencyViewer() {
  const [robotNumber, setRobotNumber] = useState('')
  const [vin, setVin] = useState('')
  const [sections, setSections] = useState<EmergencySection[]>([])
  const [snapshot, setSnapshot] = useState<EmergencySnapshot | null>(null)
  const [detail, setDetail] = useState<EmergencySectionDetail | null>(null)
  const [activeTab, setActiveTab] = useState('map')
  const [cookieStale, setCookieStale] = useState(false)
  const [staleHint, setStaleHint] = useState(false)
  const [resolving, setResolving] = useState(false)
  const [sectionLoading, setSectionLoading] = useState(false)
  const [error, setError] = useState('')
  const [follow, setFollow] = useState(true)
  const userPanRef = useRef(false)

  const onUserPan = useCallback(() => {
    userPanRef.current = true
    setFollow(false)
  }, [])

  const enableFollow = useCallback(() => {
    userPanRef.current = false
    setFollow(true)
  }, [])

  useEffect(() => {
    setSnapshot((current) => (current != null && current.vin !== vin ? null : current))
  }, [vin])

  useEffect(() => {
    if (!vin || cookieStale) return

    let cancelled = false

    const loadSnapshot = async () => {
      if (document.hidden) return
      try {
        const next = await api.emergencySnapshot(vin)
        if (cancelled) return
        setSnapshot(next)
        if (!userPanRef.current) setFollow(true)
        setStaleHint(false)
      } catch (caught) {
        if (cancelled) return
        if (isCookieInvalid(caught)) {
          setCookieStale(true)
          return
        }
        setStaleHint(true)
      }
    }

    void loadSnapshot()
    const timer = window.setInterval(() => {
      void loadSnapshot()
    }, SNAPSHOT_POLL_MS)

    const onVisibility = () => {
      if (!document.hidden) void loadSnapshot()
    }
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      cancelled = true
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [vin, cookieStale])

  const markCookieStale = () => {
    setCookieStale(true)
    setError('')
  }

  const fetchSection = async (sectionId: string, sectionVin = vin) => {
    if (!sectionVin) return
    setSectionLoading(true)
    try {
      const data = await api.emergencySection(sectionVin, sectionId)
      setDetail(data)
      setError('')
    } catch (caught) {
      if (isCookieInvalid(caught)) {
        markCookieStale()
        return
      }
      setDetail(null)
      setError(mapApiError(caught, ru.errors.emergencySection))
    } finally {
      setSectionLoading(false)
    }
  }

  const openTab = (tabId: string) => {
    setActiveTab(tabId)
    if (tabId !== 'map') {
      void fetchSection(tabId)
    }
  }

  const resolveRobot = async () => {
    const query = robotNumber.trim()
    if (!query) {
      setVin('')
      setSections([])
      setSnapshot(null)
      setDetail(null)
      setStaleHint(false)
      setActiveTab('map')
      enableFollow()
      return
    }

    setResolving(true)
    setError('')
    setSnapshot((current) =>
      current && (current.vin === query || current.short_number === query)
        ? current
        : null,
    )
    try {
      const data = await api.emergencyResolve(query)
      setVin(data.vin)
      setSections(data.sections)
      setActiveTab('map')
      setDetail(null)
      setStaleHint(false)
      enableFollow()
      setSnapshot((current) => (current?.vin === data.vin ? current : null))
      try {
        const next = await api.emergencySnapshot(data.vin)
        setSnapshot(next)
        if (!userPanRef.current) setFollow(true)
      } catch (caught) {
        if (isCookieInvalid(caught)) {
          markCookieStale()
          return
        }
        setSnapshot((current) => (current?.vin === data.vin ? current : null))
        setStaleHint(true)
      }
    } catch (caught) {
      if (isCookieInvalid(caught)) {
        markCookieStale()
        return
      }
      setVin('')
      setSections([])
      setSnapshot(null)
      setDetail(null)
      setError(mapApiError(caught, ru.errors.emergency))
    } finally {
      setResolving(false)
    }
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    await resolveRobot()
  }

  const liveSnapshot = snapshot?.vin === vin ? snapshot : null
  const identity = liveSnapshot?.short_number
    ? `${liveSnapshot.short_number} · ${vin}`
    : vin
  const formLocked = resolving || cookieStale
  const lat = liveSnapshot?.lat ?? null
  const lon = liveSnapshot?.lon ?? null
  const hasCoords = lat != null && lon != null
  const wheelsTab = wheelsSectionId(sections)

  return (
    <PageShell subtitle={ru.emergency.subtitle} title={ru.emergency.title}>
      <Panel
        actions={
          vin ? (
            <span className="panel-hint">{identity}</span>
          ) : undefined
        }
        hint={ru.emergency.searchHint}
        title={ru.emergency.searchTitle}
      >
        <form className="search-form" onSubmit={(event) => void submit(event)}>
          <input
            aria-label={ru.emergency.robotNumber}
            disabled={formLocked}
            onChange={(event) => setRobotNumber(event.target.value)}
            placeholder={ru.emergency.robotPlaceholder}
            required
            value={robotNumber}
          />
          <button className="btn" disabled={formLocked || !robotNumber.trim()} type="submit">
            {resolving ? <Spinner label="Поиск" /> : ru.emergency.resolve}
          </button>
          {vin && (
            <button
              className="btn btn-secondary"
              disabled={formLocked}
              onClick={() => void resolveRobot()}
              type="button"
            >
              {ru.emergency.refresh}
            </button>
          )}
        </form>
      </Panel>

      {error && !cookieStale && <Alert tone="error">{error}</Alert>}

      {resolving && !vin && <SkeletonList rows={2} />}

      {!resolving && !vin && !error && (
        <EmptyBlock
          hint="Номер робота преобразуется в VIN; справа откроются карта и разделы Emergency."
          icon="⚑"
          title={ru.emergency.enterRobot}
        />
      )}

      {(vin || cookieStale) && (
        <div className="inspection-desk">
          <RobotSchematic
            dimmed={cookieStale}
            onWheelClick={() => openTab(wheelsTab)}
            snapshot={liveSnapshot}
          />
          <div className="inspection-right">
            {cookieStale ? (
              <CookieStaleStub />
            ) : (
              <>
                <div className="inspection-tabs task-filters">
                  <button
                    className={`btn btn-filter${activeTab === 'map' ? ' is-active' : ''}`}
                    onClick={() => openTab('map')}
                    type="button"
                  >
                    {ru.emergency.map}
                  </button>
                  {sections.map((section) => (
                    <button
                      className={`btn btn-filter${activeTab === section.id ? ' is-active' : ''}`}
                      disabled={sectionLoading}
                      key={section.id}
                      onClick={() => openTab(section.id)}
                      type="button"
                    >
                      {section.title}
                    </button>
                  ))}
                </div>

                {staleHint && (
                  <Alert tone="warning">{ru.emergency.staleHint}</Alert>
                )}

                {activeTab === 'map' && (
                  <div className="inspection-map-pane">
                    {hasCoords ? (
                      <>
                        <button
                          className="btn btn-secondary inspection-follow"
                          onClick={enableFollow}
                          type="button"
                        >
                          {ru.emergency.follow}
                        </button>
                        <InspectionMap
                          follow={follow}
                          lat={lat}
                          lon={lon}
                          onUserPan={onUserPan}
                          visible
                        />
                      </>
                    ) : (
                      <EmptyBlock icon="🗺" title={ru.emergency.noCoords} />
                    )}
                  </div>
                )}

                {activeTab !== 'map' && (
                  <Panel title={detail?.title ?? ru.loading}>
                    {sectionLoading && <SkeletonList rows={2} />}
                    {!sectionLoading && detail?.id === activeTab && (
                      detail.fields.length ? (
                        detail.fields.map((field) => (
                          <div className="detail-block" key={field.label}>
                            <strong>{field.label}</strong>
                            <pre>{field.lines.join('\n')}</pre>
                          </div>
                        ))
                      ) : (
                        <EmptyBlock icon="📭" title={ru.emergency.detailEmpty} />
                      )
                    )}
                    {!sectionLoading && detail?.id !== activeTab && !error && (
                      <EmptyBlock icon="⏳" title={ru.loading} />
                    )}
                  </Panel>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </PageShell>
  )
}
