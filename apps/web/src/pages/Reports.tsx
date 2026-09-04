import { useState } from 'react'
import { api, type Report } from '../api'
import { useAuth } from '../auth-context'
import { ReportDetail } from '../components/reports/ReportDetail'
import { ReportForms } from '../components/reports/ReportForms'
import { ReportList } from '../components/reports/ReportList'
import { Alert, Panel } from '../components/PageShell'
import { EmptyBlock, SkeletonList, Spinner } from '../components/ui/Feedback'
import { mapApiError } from '../i18n/errors'
import { ru } from '../i18n/ru'
import { useCachedResource, resourceStore } from '../lib/resource'
import { useParkContext } from '../park-context'
import { refreshReportsBadge } from '../reports-badge'

function hasInbox(user: { permissions?: string[] } | null | undefined): boolean {
  return (user?.permissions ?? []).includes('reports.resolve')
}

function canCreateReports(user: { role: string; permissions?: string[] } | null | undefined): boolean {
  return Boolean(user?.permissions?.includes('reports.create'))
}

export function Reports() {
  const { user } = useAuth()
  const { parkId, parks, parksLoading } = useParkContext()
  const role = user?.role ?? ''
  const reportScope = user
    ? `${user.id}:${[...(user.permissions ?? [])].sort().join(',')}:${parkId ?? 'all'}`
    : ''
  const inboxEnabled = hasInbox(user)
  const createEnabled = canCreateReports(user)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [selectedScope, setSelectedScope] = useState('')
  const [selectedActionable, setSelectedActionable] = useState(false)
  const [activePane, setActivePane] = useState<'mine' | 'inbox'>('mine')
  const [statusFilter, setStatusFilter] = useState('all')

  const selectedPark = parks.find((park) => park.id === parkId)
  const visiblePane = activePane === 'mine' && createEnabled
    ? 'mine'
    : inboxEnabled
      ? 'inbox'
      : createEnabled
        ? 'mine'
        : null
  const selectedIsCurrent = selectedId != null && selectedScope === reportScope

  const mineRes = useCachedResource<Report[]>(
    createEnabled ? `reports:${reportScope}:mine` : '',
    () => api.reportsMine(),
    { enabled: createEnabled && !parksLoading, persist: false },
  )
  const inboxKey = inboxEnabled && parkId != null ? `reports:${reportScope}:inbox` : ''
  const inboxRes = useCachedResource<Report[]>(
    inboxKey,
    () => api.reportsInbox(parkId as number),
    { enabled: inboxEnabled && parkId != null && !parksLoading, persist: false },
  )
  const detailRes = useCachedResource<Report>(
    selectedIsCurrent ? `reports:${reportScope}:detail:${selectedId}` : '',
    () => api.report(selectedId as number),
    { enabled: selectedIsCurrent, persist: false },
  )

  const mine = mineRes.data ?? []
  const inbox = inboxRes.data ?? []
  const visibleMine = statusFilter === 'all' ? mine : mine.filter((report) => report.status === statusFilter)
  const visibleInbox = inbox.filter((report) => report.status === 'open')
  const selectedReport = detailRes.data ?? null

  const listRes = visiblePane === 'mine' ? mineRes : inboxRes
  const listError = listRes.error ? mapApiError(listRes.error, ru.errors.load) : ''
  const listLoading = listRes.isRevalidating
  const showListSkeleton = listRes.isLoading && !listRes.data && !listError

  const detailError = detailRes.error ? mapApiError(detailRes.error, ru.errors.load) : ''
  const detailLoading = detailRes.isLoading && !selectedReport && !detailError

  function handleSelect(report: Report, canAct: boolean) {
    setSelectedId(report.id)
    setSelectedScope(reportScope)
    setSelectedActionable(canAct)
  }

  function handleCloseDetail() {
    setSelectedId(null)
    setSelectedScope('')
    setSelectedActionable(false)
  }

  function parkNameForReport(report: Report): string {
    if (report.park_id == null) return 'Платформа'
    return parks.find((park) => park.id === report.park_id)?.name ?? `Парк #${report.park_id}`
  }

  async function refreshLists() {
    await Promise.all([
      createEnabled ? mineRes.refresh() : Promise.resolve(),
      inboxKey ? inboxRes.refresh() : Promise.resolve(),
    ])
  }

  async function handleDetailUpdated() {
    resourceStore.invalidate('reports:', { prefix: true })
    await refreshLists()
    refreshReportsBadge()
    if (selectedId != null) {
      try {
        await detailRes.refresh()
        const fresh = resourceStore.get<Report>(`reports:${reportScope}:detail:${selectedId}`)
        if (fresh && fresh.status !== 'open') {
          handleCloseDetail()
        }
      } catch {
        handleCloseDetail()
      }
    }
  }

  async function handleCreated() {
    resourceStore.invalidate('reports:', { prefix: true })
    await refreshLists()
    refreshReportsBadge()
  }

  const isAdminInbox = role === 'admin' || role === 'royal'
  const inboxTitle = isAdminInbox ? 'Эскалации' : 'Входящие'
  const inboxHint = isAdminInbox
    ? 'Открытые эскалации от операторов. Фильтр по парку — в верхней панели.'
    : 'Открытые репорты механиков по выбранному парку.'

  return (
    <div className="dashboard-page animate-in">
      <div className="dashboard-toolbar">
        <h1 className="dashboard-title">{ru.nav.reports}</h1>
        <button
          className="btn btn-secondary"
          disabled={listLoading || parksLoading || (inboxEnabled && parkId == null)}
          onClick={() => void refreshLists()}
          type="button"
        >
          {listLoading ? <Spinner label="Обновление" /> : 'Обновить'}
        </button>
      </div>

      {createEnabled && inboxEnabled && (
        <div aria-label="Режим репортов" className="actions" role="tablist">
          <button
            aria-selected={visiblePane === 'mine'}
            className={`btn btn-filter${visiblePane === 'mine' ? ' is-active' : ''}`}
            onClick={() => { handleCloseDetail(); setActivePane('mine') }}
            role="tab"
            type="button"
          >
            Мои репорты
          </button>
          <button
            aria-selected={visiblePane === 'inbox'}
            className={`btn btn-filter${visiblePane === 'inbox' ? ' is-active' : ''}`}
            onClick={() => { handleCloseDetail(); setActivePane('inbox') }}
            role="tab"
            type="button"
          >
            {inboxTitle}
          </button>
        </div>
      )}

      {visiblePane === 'mine' && <label className="field">
        <span className="field-label">Статус</span>
        <select aria-label="Статус репортов" onChange={(event) => setStatusFilter(event.target.value)} value={statusFilter}>
          <option value="all">Все</option>
          <option value="open">Открытые</option>
          <option value="returned">Возвращённые</option>
          <option value="done">Готовые</option>
        </select>
      </label>}

      {listError && <Alert tone="error">{listError}</Alert>}

      {createEnabled && visiblePane === 'mine' && (
        <>
          <Panel hint="Статусы ваших репортов и комментарии при возврате." title="Мои репорты">
            <ReportList
              emptyMessage="Вы ещё не создавали репортов."
              loading={showListSkeleton}
              onSelect={(report) => handleSelect(report, false)}
              reports={visibleMine}
              selectedId={selectedId}
              showReturnComment
            />
          </Panel>

          {parkId != null ? (
            <Panel
              hint={`Парк: ${selectedPark?.name ?? parkId}. Репорт уходит оператору парка.`}
              title="Создать репорт"
            >
              <ReportForms onCreated={() => void handleCreated()} parkId={parkId} />
            </Panel>
          ) : (
            <Panel title="Создать репорт">
              <EmptyBlock
                hint="Репорт уходит оператору парка — без назначенного парка адресата нет."
                icon="🏭"
                title="Парк не назначен"
              />
            </Panel>
          )}

          {selectedIsCurrent && (
            <Panel title="Репорт">
              {detailError && <Alert tone="error">{detailError}</Alert>}
              {detailLoading && <SkeletonList rows={2} />}
              {!detailLoading && selectedReport && (
                <ReportDetail
                  canAct={false}
                  onClose={handleCloseDetail}
                  onUpdated={() => void handleDetailUpdated()}
                  parkName={parkNameForReport(selectedReport)}
                  report={selectedReport}
                  showEscalate={false}
                />
              )}
            </Panel>
          )}
        </>
      )}

      {inboxEnabled && visiblePane === 'inbox' && (
        <>
          {parkId == null && !parksLoading && (
            <EmptyBlock
              hint="Входящие репорты показываются по выбранному парку."
              icon="📥"
              title="Выберите парк в верхней панели"
            />
          )}

          {parkId != null && !selectedIsCurrent && (
            <Panel hint={inboxHint} title={inboxTitle}>
              <ReportList
                emptyMessage="Нет открытых репортов для выбранного парка."
                loading={showListSkeleton}
                onSelect={(report) => handleSelect(report, true)}
                reports={visibleInbox}
                selectedId={selectedId}
                showReturnComment={false}
              />
            </Panel>
          )}

          {parkId != null && selectedIsCurrent && (
            <Panel title="Репорт">
              {detailError && <Alert tone="error">{detailError}</Alert>}
              {detailLoading && <SkeletonList rows={2} />}
              {!detailLoading && selectedReport && (
                <ReportDetail
                  canAct={selectedActionable}
                  onClose={handleCloseDetail}
                  onUpdated={() => void handleDetailUpdated()}
                  parkName={parkNameForReport(selectedReport)}
                  report={selectedReport}
                  showEscalate={role === 'operator'}
                />
              )}
            </Panel>
          )}
        </>
      )}

      {!createEnabled && !inboxEnabled && user && (
        <EmptyBlock
          hint="Для этой роли нет действий с репортами."
          icon="✉"
          title="Раздел недоступен"
        />
      )}

      {!role && !user && <SkeletonList rows={3} />}
    </div>
  )
}
