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
import { resourceStore, useCachedResource } from '../../lib/resource'
import { CookieStaleStub } from './CookieStaleStub'
import { InspectionMap } from './InspectionMap'
import { RobotSchematic } from './RobotSchematic'

const SNAPSHOT_POLL_MS = 2500

type ResolvePayload = {
  vin: string
  sections: EmergencySection[]
}

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

function sectionIdByHints(
  sections: EmergencySection[],
  ids: string[],
  titlePattern: RegExp,
): string | null {
  const byId = sections.find((section) => ids.includes(section.id))
  if (byId) return byId.id
  const byTitle = sections.find((section) => titlePattern.test(section.title))
  return byTitle?.id ?? null
}

export function EmergencyViewer() {
  const [robotNumber, setRobotNumber] = useState('')
  const [vin, setVin] = useState('')
  const [sections, setSections] = useState<EmergencySection[]>([])
  const [activeTab, setActiveTab] = useState('map')
  const [cookieStale, setCookieStale] = useState(false)
  const [resolving, setResolving] = useState(false)
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

  const markCookieStale = useCallback(() => {
    setCookieStale(true)
    resourceStore.invalidate('emergency:', { prefix: true })
    setError('')
  }, [])

  const snapshotRes = useCachedResource<EmergencySnapshot>(
    vin && !cookieStale ? `emergency:snapshot:${vin}` : '',
    () => api.emergencySnapshot(vin),
    { enabled: Boolean(vin) && !cookieStale, persist: false, trackProgress: false },
  )
  const sectionRes = useCachedResource<EmergencySectionDetail>(
    vin && activeTab !== 'map' && !cookieStale ? `emergency:section:${vin}:${activeTab}` : '',
    () => api.emergencySection(vin, activeTab),
    { enabled: Boolean(vin) && activeTab !== 'map' && !cookieStale, persist: false },
  )

  useEffect(() => {
    if (isCookieInvalid(snapshotRes.error) || isCookieInvalid(sectionRes.error)) {
      markCookieStale()
    }
  }, [snapshotRes.error, sectionRes.error, markCookieStale])

  useEffect(() => {
    if (!vin || cookieStale) return
    const tick = () => {
      if (!document.hidden) void snapshotRes.refresh()
    }
    const timer = window.setInterval(tick, SNAPSHOT_POLL_MS)
    const onVisibility = () => {
      if (!document.hidden) void snapshotRes.refresh()
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [vin, cookieStale, snapshotRes.refresh])

  const resolveRobot = async () => {
    const query = robotNumber.trim()
    if (!query) {
      setVin('')
      setSections([])
      setActiveTab('map')
      enableFollow()
      return
    }

    setResolving(true)
    setError('')
    const cached = resourceStore.get<ResolvePayload>(`emergency:resolve:${query}`)
    if (cached) {
      setVin(cached.vin)
      setSections(cached.sections)
      setCookieStale(false)
      setActiveTab('map')
      enableFollow()
    }
    try {
      const data = await api.emergencyResolve(query)
      resourceStore.set(`emergency:resolve:${query}`, data, false)
      setVin(data.vin)
      setSections(data.sections)
      setActiveTab('map')
      setCookieStale(false)
      enableFollow()
    } catch (caught) {
      if (isCookieInvalid(caught)) {
        setVin('')
        setSections([])
        markCookieStale()
        return
      }
      if (!cached) {
        setVin('')
        setSections([])
      }
      setError(mapApiError(caught, ru.errors.emergency))
    } finally {
      setResolving(false)
    }
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    await resolveRobot()
  }

  const liveSnapshot = snapshotRes.data?.vin === vin ? snapshotRes.data : null
  const detail = sectionRes.data?.id === activeTab ? sectionRes.data : null
  const sectionLoading = sectionRes.isLoading && !detail
  const sectionError =
    sectionRes.error && !isCookieInvalid(sectionRes.error)
      ? mapApiError(sectionRes.error, ru.errors.emergencySection)
      : ''
  const staleHint = Boolean(snapshotRes.error) && !isCookieInvalid(snapshotRes.error)
  const identity = liveSnapshot?.short_number
    ? `${liveSnapshot.short_number} · ${vin}`
    : vin
  const formLocked = resolving || cookieStale
  const lat = liveSnapshot?.lat ?? null
  const lon = liveSnapshot?.lon ?? null
  const hasCoords = lat != null && lon != null
  const wheelsTab = wheelsSectionId(sections)
  const soundTab = sectionIdByHints(
    sections,
    ['hardware_hud', 'hardware'],
    /оборуд|hud|звук|сирен/i,
  )
  const powerTab = sectionIdByHints(
    sections,
    ['control', 'errors'],
    /управл|ошиб|control/i,
  )
  const displayError = error || sectionError

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

      {displayError && !cookieStale && <Alert tone="error">{displayError}</Alert>}

      {resolving && !vin && <SkeletonList rows={2} />}

      {!resolving && !vin && !displayError && (
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
            onPowerClick={powerTab ? () => setActiveTab(powerTab) : undefined}
            onSoundClick={soundTab ? () => setActiveTab(soundTab) : undefined}
            onWheelClick={() => setActiveTab(wheelsTab)}
            snapshot={liveSnapshot}
          />
          <div className="inspection-right">
            {cookieStale ? (
              <CookieStaleStub />
            ) : (
              <>
                <div className="inspection-tabs task-filters" role="tablist">
                  <button
                    aria-selected={activeTab === 'map'}
                    className={`btn btn-filter${activeTab === 'map' ? ' is-active' : ''}`}
                    onClick={() => setActiveTab('map')}
                    role="tab"
                    type="button"
                  >
                    {ru.emergency.map}
                  </button>
                  {sections.map((section) => (
                    <button
                      aria-selected={activeTab === section.id}
                      className={`btn btn-filter${activeTab === section.id ? ' is-active' : ''}`}
                      key={section.id}
                      onClick={() => setActiveTab(section.id)}
                      role="tab"
                      type="button"
                    >
                      {section.title}
                    </button>
                  ))}
                </div>

                {staleHint && (
                  <Alert tone="warning">{ru.emergency.staleHint}</Alert>
                )}

                {hasCoords ? (
                  <div
                    className="inspection-map-pane"
                    hidden={activeTab !== 'map'}
                  >
                    <button
                      aria-pressed={follow}
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
                      visible={activeTab === 'map'}
                    />
                  </div>
                ) : (
                  activeTab === 'map' && (
                    snapshotRes.isLoading ? (
                      <SkeletonList rows={2} />
                    ) : (
                      <EmptyBlock icon="🗺" title={ru.emergency.noCoords} />
                    )
                  )
                )}

                {activeTab !== 'map' && (
                  <Panel title={detail?.title ?? ru.loading}>
                    {sectionLoading && <SkeletonList rows={2} />}
                    {detail && (
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
                    {!sectionLoading && !detail && !sectionError && (
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
