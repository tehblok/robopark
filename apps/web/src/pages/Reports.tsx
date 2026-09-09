import { SyncStatus } from '../design-system/status/SyncStatus'
import { deleteReportPhotoDraft } from '../domains/reports/reportPhotoDrafts'
import { useCallback, useLayoutEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, type Park, type Report, type User } from '../api'
import { useAuth } from '../auth-context'
import { ReportDetail } from '../components/reports/ReportDetail'
import { ReportForms } from '../components/reports/ReportForms'
import { ReportList } from '../components/reports/ReportList'
import { Alert, Panel } from '../components/PageShell'
import { EmptyBlock, SkeletonList } from '../components/ui/Feedback'
import { TabPanel, Tabs } from '../design-system/navigation/Tabs'
import { MasterDetail } from '../design-system/layout/MasterDetail'
import {
  reportDraftKey,
  reportsAccessIdentity,
  type ReportsApiClient,
} from '../domains/reports/reports'
import { mapApiError } from '../i18n/errors'
import { ru } from '../i18n/ru'
import { useCachedResource, resourceStore, RESOURCE_REFRESH_MS } from '../lib/resource'
import { useParkContext } from '../park-context'
import { refreshReportsBadge } from '../reports-badge'

type ReportsPane = 'mine' | 'inbox'
type ReportsStatus = 'all' | 'open' | 'returned' | 'done'

const REPORT_STATUSES = new Set<ReportsStatus>(['all', 'open', 'returned', 'done'])

function hasInbox(user: User): boolean {
  return (user.permissions ?? []).includes('reports.resolve')
}

function canCreateReports(user: User): boolean {
  return Boolean(user.permissions?.includes('reports.create'))
}

function searchString(params: URLSearchParams): string {
  const value = params.toString()
  return value ? `?${value}` : ''
}

function ReportsOwner({
  apiClient,
  identity,
  parkId,
  parks,
  parksLoading,
  resourcePrefix,
  restoreDraft,
  user,
}: {
  apiClient: ReportsApiClient
  identity: string
  parkId: number | null
  parks: Park[]
  parksLoading: boolean
  resourcePrefix: string
  restoreDraft: boolean
  user: User
}) {
  const location = useLocation()
  const active = useRef(true)
  const navigation = useRef({ identity, key: location.key })
  useLayoutEffect(() => {
    navigation.current = { identity, key: location.key }
  }, [identity, location.key])
  useLayoutEffect(() => {
    active.current = true
    return () => { active.current = false }
  }, [])
  const navigate = useNavigate()
  const { reportId: reportIdParam } = useParams()
  const [params, setParams] = useSearchParams()
  const createEnabled = canCreateReports(user)
  const inboxEnabled = hasInbox(user)
  const listRoute = location.pathname === '/reports'
  const createRoute = location.pathname === '/reports/new'
  const parsedReportId = reportIdParam && /^\d+$/.test(reportIdParam)
    ? Number(reportIdParam)
    : null
  const detailRoute = parsedReportId != null && parsedReportId > 0
  const requestedPane = params.get('pane')
  const visiblePane: ReportsPane | null = requestedPane === 'inbox' && inboxEnabled
    ? 'inbox'
    : createEnabled
      ? 'mine'
      : inboxEnabled
        ? 'inbox'
        : null
  const requestedStatus = params.get('status') as ReportsStatus | null
  const statusFilter: ReportsStatus = requestedStatus && REPORT_STATUSES.has(requestedStatus)
    ? requestedStatus
    : 'all'
  const selectedPark = parks.find((park) => park.id === parkId) ?? null

  const mineKey = `${resourcePrefix}mine`
  const mineRes = useCachedResource<Report[]>(
    mineKey,
    () => apiClient.reportsMine(),
    { enabled: (listRoute || detailRoute) && createEnabled && !parksLoading, persist: false, refreshIntervalMs: listRoute && visiblePane === 'mine' ? RESOURCE_REFRESH_MS : 0 },
  )
  const inboxKey = `${resourcePrefix}inbox`
  const inboxRes = useCachedResource<Report[]>(
    inboxKey,
    () => apiClient.reportsInbox(parkId as number),
    { enabled: (listRoute || detailRoute) && inboxEnabled && parkId != null && !parksLoading, persist: false, refreshIntervalMs: listRoute && visiblePane === 'inbox' ? RESOURCE_REFRESH_MS : 0 },
  )
  const detailKey = `${resourcePrefix}detail:${parsedReportId ?? 'none'}`
  const detailRes = useCachedResource<Report>(
    detailKey,
    () => apiClient.report(parsedReportId as number),
    { enabled: detailRoute, persist: false },
  )

  const updateSearch = (update: (next: URLSearchParams) => void) => {
    const next = new URLSearchParams(params)
    update(next)
    setParams(next, { replace: true })
  }
  const choosePane = (pane: ReportsPane) => {
    const next = new URLSearchParams(params)
    if (pane === 'inbox') {
      next.set('pane', 'inbox')
      next.delete('status')
    } else {
      next.delete('pane')
    }
    navigate({ pathname: '/reports', search: searchString(next) })
  }
  const openReport = (report: Report, actionable: boolean) => {
    const next = new URLSearchParams(params)
    if (actionable) next.set('pane', 'inbox')
    else next.delete('pane')
    navigate({ pathname: `/reports/${report.id}`, search: searchString(next) })
  }
  const closeDetail = () => {
    navigate({ pathname: '/reports', search: searchString(params) })
  }
  const parkNameForReport = (report: Report): string => {
    if (report.park_id == null) return 'Платформа'
    return parks.find((park) => park.id === report.park_id)?.name ?? `Парк #${report.park_id}`
  }
  const refreshLists = useCallback(async () => {
    for (const key of [mineKey, inboxKey]) {
      const cached = resourceStore.get<Report[]>(key)
      resourceStore.invalidate(key)
      if (cached) resourceStore.set(key, cached, false)
    }
    await Promise.all([
      createEnabled ? mineRes.refresh() : Promise.resolve(),
      inboxEnabled && parkId != null ? inboxRes.refresh() : Promise.resolve(),
    ])
  }, [createEnabled, inboxEnabled, inboxKey, inboxRes, mineKey, mineRes, parkId])
  const handleDetailUpdated = async () => {
    const requestedNavigation = navigation.current
    const isCurrent = () => active.current && navigation.current === requestedNavigation
    resourceStore.invalidate(detailKey)
    await detailRes.refresh()
    if (!isCurrent()) return
    await refreshLists()
    if (!isCurrent()) return
    refreshReportsBadge()
    const fresh = resourceStore.get<Report>(detailKey)
    if (fresh && fresh.status !== 'open') closeDetail()
  }
  const handleCreated = () => {
    resourceStore.invalidate(mineKey)
    refreshReportsBadge()
  }

  const mine = mineRes.data ?? []
  const inbox = (inboxRes.data ?? []).filter((report) => report.status === 'open')
  const visibleMine = statusFilter === 'all'
    ? mine
    : mine.filter((report) => report.status === statusFilter)
  const activeList = visiblePane === 'mine' ? mineRes : inboxRes
  const listError = activeList.error ? mapApiError(activeList.error, ru.errors.load) : ''
  const showListSkeleton = activeList.isLoading && !activeList.data && !listError
  const selectedReport = detailRes.data ?? null
  const detailError = detailRes.error ? mapApiError(detailRes.error, ru.errors.load) : ''
  const detailLoading = detailRes.isLoading && !selectedReport && !detailError
  const role = user.role
  const isAdminInbox = role === 'admin' || role === 'royal'
  const inboxTitle = isAdminInbox ? 'Эскалации' : 'Входящие'
  const inboxHint = isAdminInbox
    ? 'Открытые эскалации от операторов. Фильтр по парку — в верхней панели.'
    : 'Открытые репорты по выбранному парку.'
  const currentSearch = searchString(params)

  if (createRoute) {
    return (
      <div className="dashboard-page rp-reports animate-in">
        <div className="dashboard-toolbar">
          <h1 className="dashboard-title">Создать репорт</h1>
          <Link className="btn btn-secondary" to={{ pathname: '/reports', search: currentSearch }}>
            К репортам
          </Link>
        </div>
        {!createEnabled ? (
          <EmptyBlock hint="Для этой роли создание репортов отключено." icon="✉" title="Нет доступа" />
        ) : parkId == null ? (
          <EmptyBlock
            hint="Репорт уходит оператору парка — без назначенного парка адресата нет."
            icon="🏭"
            title="Парк не назначен"
          />
        ) : (
          <Panel
            collapsible
            hint={`Парк: ${selectedPark?.name ?? parkId}. Репорт уходит оператору парка.`}
            storageKey="reports-create"
            title="Данные репорта"
          >
            <ReportForms
              apiClient={apiClient}
              onCreated={handleCreated}
              ownerKey={identity}
              parkId={parkId}
              principalId={user.id}
              restoreDraft={restoreDraft}
            />
          </Panel>
        )}
      </div>
    )
  }

  return (
    <div className="dashboard-page rp-reports animate-in">
      <div className="dashboard-toolbar">
        <h1 className="dashboard-title" id="reports-title">{ru.nav.reports}</h1>
        <div className="actions">
          {createEnabled && (
            <Link className="btn" to={{ pathname: '/reports/new', search: currentSearch }}>
              Создать репорт
            </Link>
          )}
        </div>
      </div>

      <MasterDetail detailOpen={detailRoute} onBack={closeDetail} list={<>
      {createEnabled && inboxEnabled && (
        <Tabs
          ariaLabel="Режим репортов"
          items={[
            { id: 'mine', label: 'Мои репорты' },
            { id: 'inbox', label: inboxTitle },
          ]}
          onChange={(pane) => choosePane(pane as ReportsPane)}
          panelIdFor={(pane) => `reports-${pane}-panel`}
          value={visiblePane ?? 'mine'}
        />
      )}

      {visiblePane === 'mine' && (
        <label className="field">
          <span className="field-label">Статус</span>
          <select
            aria-label="Статус репортов"
            onChange={(event) => updateSearch((next) => {
              const status = event.target.value as ReportsStatus
              if (status === 'all') next.delete('status')
              else next.set('status', status)
            })}
            value={statusFilter}
          >
            <option value="all">Все</option>
            <option value="open">Открытые</option>
            <option value="returned">Возвращённые</option>
            <option value="done">Готовые</option>
          </select>
        </label>
      )}

      <SyncStatus {...activeList} />
      {listError && <Alert tone="error">{listError}</Alert>}

      {createEnabled && (
        <TabPanel
          active={visiblePane === 'mine'}
          id="reports-mine-panel"
          labelledBy={createEnabled && inboxEnabled ? 'tab-mine' : 'reports-title'}
        >
          <Panel collapsible hint="Статусы ваших репортов и комментарии при возврате." storageKey="reports-mine" title="Мои репорты">
            <ReportList
              emptyMessage="Вы ещё не создавали репортов."
              loading={showListSkeleton && visiblePane === 'mine'}
              onSelect={(report) => openReport(report, false)}
              reports={visibleMine}
              selectedId={parsedReportId}
              showReturnComment
            />
          </Panel>
        </TabPanel>
      )}

      {inboxEnabled && (
        <TabPanel
          active={visiblePane === 'inbox'}
          id="reports-inbox-panel"
          labelledBy={createEnabled && inboxEnabled ? 'tab-inbox' : 'reports-title'}
        >
          {parkId == null && !parksLoading ? (
            <EmptyBlock
              hint="Входящие репорты показываются по выбранному парку."
              icon="📥"
              title="Выберите парк в верхней панели"
            />
          ) : (
            <Panel collapsible hint={inboxHint} storageKey="reports-inbox" title={inboxTitle}>
              <ReportList
                emptyMessage="Нет открытых репортов для выбранного парка."
                loading={showListSkeleton && visiblePane === 'inbox'}
                onSelect={(report) => openReport(report, true)}
                reports={inbox}
                selectedId={parsedReportId}
                showReturnComment={false}
              />
            </Panel>
          )}
        </TabPanel>
      )}

      {!createEnabled && !inboxEnabled && (
        <EmptyBlock hint="Для этой роли нет действий с репортами." icon="✉" title="Раздел недоступен" />
      )}
      </>} detail={
        <Panel collapsible storageKey="reports-detail" title="Детали репорта">
          {detailError && <Alert tone="error">{detailError}</Alert>}
          {detailLoading && <SkeletonList rows={2} />}
          {!detailRoute && <EmptyBlock title="Выберите репорт" hint="Откройте репорт из списка, чтобы прочитать детали и выполнить доступные действия." />}
          {detailRoute && !detailLoading && selectedReport && (
            <ReportDetail
              apiClient={apiClient}
              canAct={visiblePane === 'inbox' && inboxEnabled}
              key={selectedReport.id}
              onClose={closeDetail}
              onUpdated={() => void handleDetailUpdated()}
              ownerKey={identity}
              parkName={parkNameForReport(selectedReport)}
              report={selectedReport}
              showEscalate={role === 'operator'}
            />
          )}
        </Panel>
      } />
    </div>
  )
}

export function Reports({ apiClient = api }: { apiClient?: ReportsApiClient } = {}) {
  const { user } = useAuth()
  const { parkId, parks, parksLoading } = useParkContext()
  const selectedPark = parks.find((park) => park.id === parkId) ?? null
  const identity = user ? reportsAccessIdentity(user, selectedPark) : ''
  const resourcePrefix = user ? `reports:${user.id}:${identity}:` : ''
  const draftKey = user && parkId != null ? reportDraftKey(user.id, parkId) : null
  const committed = useRef({ identity, resourcePrefix, draftKey })
  const [visibleOwner, setVisibleOwner] = useState({ identity, draftKey })
  const ownerChanged = visibleOwner.identity !== identity
  const restoreDraft = !ownerChanged || visibleOwner.draftKey !== draftKey

  useLayoutEffect(() => {
    if (committed.current.identity === identity) return
    if (committed.current.resourcePrefix) {
      resourceStore.invalidate(committed.current.resourcePrefix, { prefix: true })
    }
    if (committed.current.draftKey) {
      void deleteReportPhotoDraft(committed.current.draftKey).catch(() => {})
      try {
        localStorage.removeItem(committed.current.draftKey)
      } catch {
        // Browser storage is optional.
      }
    }
    committed.current = { identity, resourcePrefix, draftKey }
    setVisibleOwner({ identity, draftKey })
  }, [draftKey, identity, resourcePrefix])

  useLayoutEffect(() => () => {
    if (committed.current.resourcePrefix) {
      resourceStore.invalidate(committed.current.resourcePrefix, { prefix: true })
    }
  }, [])

  if (!user) return <SkeletonList rows={3} />
  return (
    <ReportsOwner
      apiClient={apiClient}
      identity={identity}
      key={identity}
      parkId={parkId}
      parks={parks}
      parksLoading={parksLoading}
      resourcePrefix={resourcePrefix}
      restoreDraft={restoreDraft}
      user={user}
    />
  )
}
