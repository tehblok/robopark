import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  ApiError,
  api,
  type EmergencySection,
  type EmergencySectionDetail,
  type EmergencySnapshot,
} from '../../api'
import { useAuth } from '../../auth-context'
import { Alert, PageShell, Panel } from '../PageShell'
import { EmptyBlock, SkeletonList, Spinner } from '../ui/Feedback'
import { mapApiError } from '../../i18n/errors'
import { ru } from '../../i18n/ru'
import { loadRecentRobots, pushRecentRobot } from '../../lib/recentRobots'
import { resourceStore, useCachedResource } from '../../lib/resource'
import { CookieStaleStub } from './CookieStaleStub'
import { InspectionMap } from './InspectionMap'
import {
  buildInspectionParams,
  inspectionParamsEqual,
  parseInspectionParams,
} from './inspectionUrl'
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

export function EmergencyViewer() {
  const { user } = useAuth()
  const isDriver = user?.role === 'driver'
  const [searchParams, setSearchParams] = useSearchParams()
  const { q: urlQuery, tab: urlTab } = parseInspectionParams(searchParams)
  const [robotNumber, setRobotNumber] = useState(urlQuery)
  const [recent, setRecent] = useState<string[]>(() => loadRecentRobots())
  const autoQueryRef = useRef('')
  const [vin, setVin] = useState('')
  const [sections, setSections] = useState<EmergencySection[]>([])
  const [activeTab, setActiveTab] = useState(urlTab)
  const [cookieStale, setCookieStale] = useState(false)
  const [resolving, setResolving] = useState(false)
  const [error, setError] = useState('')
  const [follow, setFollow] = useState(true)
  const userPanRef = useRef(false)

  const writeUrl = useCallback(
    (query: string, tab: string) => {
      const next = buildInspectionParams(query, tab)
      if (inspectionParamsEqual(searchParams, next)) return
      setSearchParams(next, { replace: true })
    },
    [searchParams, setSearchParams],
  )

  const selectTab = useCallback(
    (tab: string) => {
      setActiveTab(tab)
      writeUrl(robotNumber.trim() || urlQuery, tab)
    },
    [robotNumber, urlQuery, writeUrl],
  )

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
    {
      enabled: Boolean(vin) && activeTab !== 'map' && !cookieStale,
      persist: false,
      trackProgress: false,
    },
  )

  useEffect(() => {
    if (isCookieInvalid(snapshotRes.error) || isCookieInvalid(sectionRes.error)) {
      markCookieStale()
    }
  }, [snapshotRes.error, sectionRes.error, markCookieStale])

  useEffect(() => {
    if (!vin || cookieStale) return
    const tick = () => {
      if (document.hidden) return
      void snapshotRes.refresh()
      if (activeTab !== 'map') void sectionRes.refresh()
    }
    const timer = window.setInterval(tick, SNAPSHOT_POLL_MS)
    const onVisibility = () => {
      if (!document.hidden) tick()
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [vin, cookieStale, activeTab, snapshotRes.refresh, sectionRes.refresh])

  const resolveRobot = useCallback(async (
    queryOverride?: string,
    opts: { resetView?: boolean } = {},
  ) => {
    const query = (queryOverride ?? robotNumber).trim()
    const resetView = opts.resetView ?? queryOverride == null
    if (queryOverride != null) setRobotNumber(query)
    autoQueryRef.current = query
    if (!query) {
      setVin('')
      setSections([])
      setActiveTab('map')
      enableFollow()
      writeUrl('', 'map')
      return
    }

    setResolving(true)
    setError('')
    const cached = resourceStore.get<ResolvePayload>(`emergency:resolve:${query}`)
    const apply = (data: ResolvePayload) => {
      setVin(data.vin)
      setSections(data.sections)
      setCookieStale(false)
      setRecent(pushRecentRobot(query))
      if (resetView) {
        setActiveTab('map')
        enableFollow()
        writeUrl(query, 'map')
        return
      }
      const restored = urlTab === 'map' || data.sections.some((section) => section.id === urlTab)
        ? urlTab
        : 'map'
      setActiveTab(restored)
      writeUrl(query, restored)
    }
    if (cached) apply(cached)
    try {
      const data = await api.emergencyResolve(query)
      resourceStore.set(`emergency:resolve:${query}`, data, false)
      apply(data)
    } catch (caught) {
      if (isCookieInvalid(caught)) {
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
  }, [enableFollow, markCookieStale, robotNumber, urlTab, writeUrl])

  useEffect(() => {
    if (!urlQuery || autoQueryRef.current === urlQuery) return
    autoQueryRef.current = urlQuery
    void resolveRobot(urlQuery, { resetView: false })
  }, [urlQuery, resolveRobot])

  useEffect(() => {
    if (!sections.length || activeTab === 'map') return
    if (sections.some((section) => section.id === activeTab)) return
    selectTab('map')
  }, [sections, activeTab, selectTab])

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
  const displayError = error || sectionError

  return (
    <PageShell
      subtitle={isDriver ? ru.emergency.driverSubtitle : ru.emergency.subtitle}
      title={ru.emergency.title}
    >
      <Panel
        actions={
          vin ? (
            <span className="panel-hint">{identity}</span>
          ) : undefined
        }
        hint={isDriver ? ru.emergency.driverSearchHint : ru.emergency.searchHint}
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
        </form>
        {recent.length > 0 && (
          <div className="chip-row">
            {recent.map((item) => (
              <button
                className="chip"
                disabled={formLocked}
                key={item}
                onClick={() => void resolveRobot(item)}
                type="button"
              >
                {item}
              </button>
            ))}
          </div>
        )}
      </Panel>

      {displayError && !cookieStale && <Alert tone="error">{displayError}</Alert>}

      {resolving && !vin && <SkeletonList rows={2} />}

      {!resolving && !vin && !displayError && (
        <EmptyBlock
          hint={
            isDriver
              ? 'Введите короткий номер или VIN — карта и секции откроются справа.'
              : 'Номер робота преобразуется в VIN; справа откроются карта и разделы Emergency.'
          }
          icon="⚑"
          title={ru.emergency.enterRobot}
        />
      )}

      {(vin || cookieStale) && (
        <div className="inspection-desk">
          <RobotSchematic
            dimmed={cookieStale}
            onWheelClick={() => selectTab(wheelsTab)}
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
                    onClick={() => selectTab('map')}
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
                      onClick={() => selectTab(section.id)}
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
