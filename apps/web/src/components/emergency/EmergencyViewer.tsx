import { type FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  ApiError,
  api,
  type EmergencySection,
  type EmergencyView,
} from '../../api'
import { useAuth } from '../../auth-context'
import { checkAccessIdentity } from '../../domains/robots/robotCheckUrl'
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

import { ROBOT_POLL_MS as SNAPSHOT_POLL_MS } from '../../domains/robots/polling'

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

function EmergencyViewerOwner() {
  const { user } = useAuth()
  const isDriver = user?.role === 'driver'
  const cacheScope = `emergency:${user?.id}:${user ? checkAccessIdentity(user) : 'anonymous'}:`
  const [denied, setDenied] = useState(false)
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
    resourceStore.invalidate(cacheScope, { prefix: true })
    setError('')
  }, [cacheScope])

  const viewRes = useCachedResource<EmergencyView>(
    vin && !cookieStale && !denied ? `${cacheScope}view:${vin}:${activeTab}` : '',
    () => api.emergencyView(vin, activeTab === 'map' ? undefined : activeTab),
    {
      enabled: Boolean(vin) && !cookieStale && !denied,
      persist: false,
      trackProgress: false,
      refreshIntervalMs: SNAPSHOT_POLL_MS,
      staleTimeMs: SNAPSHOT_POLL_MS,
    },
  )

  useEffect(() => {
    if (isCookieInvalid(viewRes.error)) {
      markCookieStale()
    }
  }, [viewRes.error, markCookieStale])

  useEffect(() => {
    const failure = viewRes.error instanceof ApiError && [401, 403].includes(viewRes.error.status) && !isCookieInvalid(viewRes.error) ? viewRes.error : null
    if (!failure) return
    setDenied(true); setVin(''); setSections([])
    resourceStore.invalidate(cacheScope, { prefix: true })
    setError(mapApiError(failure, ru.errors.emergency))
  }, [viewRes.error, cacheScope])

  const resolveRobot = useCallback(async (
    queryOverride?: string,
    opts: { resetView?: boolean } = {},
  ) => {
    if (denied) return
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
    const cached = resourceStore.get<ResolvePayload>(`${cacheScope}resolve:${query}`)
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
      resourceStore.set(`${cacheScope}resolve:${query}`, data, false)
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
  }, [cacheScope, denied, enableFollow, markCookieStale, robotNumber, urlTab, writeUrl])

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

  const liveSnapshot = viewRes.data?.snapshot.vin === vin ? viewRes.data.snapshot : null
  const detail = viewRes.data?.section?.id === activeTab ? viewRes.data.section : null
  const sectionLoading = viewRes.isLoading && !detail
  const sectionError =
    activeTab !== 'map' && viewRes.error && !isCookieInvalid(viewRes.error)
      ? mapApiError(viewRes.error, ru.errors.emergencySection)
      : ''
  const staleHint = Boolean(viewRes.error) && !isCookieInvalid(viewRes.error)
  const identity = liveSnapshot?.short_number
    ? `${liveSnapshot.short_number} · ${vin}`
    : vin
  const formLocked = resolving || cookieStale || denied
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
                    viewRes.isLoading ? (
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

export function EmergencyViewer() {
  const { user } = useAuth()
  return <EmergencyViewerOwner key={`${user?.id}:${user ? checkAccessIdentity(user) : 'anonymous'}`} />
}
