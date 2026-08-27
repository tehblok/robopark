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

function isInboxRole(role: string): boolean {
  return role === 'operator' || role === 'admin' || role === 'royal'
}

function isAdminRole(role: string): boolean {
  return role === 'admin' || role === 'royal'
}

export function Reports() {
  const { user } = useAuth()
  const { parkId, parks, parksLoading } = useParkContext()
  const role = user?.role ?? ''
  const [selectedId, setSelectedId] = useState<number | null>(null)

  const selectedPark = parks.find((park) => park.id === parkId)

  const mineRes = useCachedResource<Report[]>(
    role === 'mechanic' ? 'reports:mine' : '',
    () => api.reportsMine(),
    { enabled: role === 'mechanic' && !parksLoading },
  )
  const inboxKey =
    isInboxRole(role) && parkId != null ? `reports:inbox:${parkId}` : ''
  const inboxRes = useCachedResource<Report[]>(
    inboxKey,
    () => api.reportsInbox(parkId as number),
    { enabled: isInboxRole(role) && parkId != null && !parksLoading },
  )
  const detailRes = useCachedResource<Report>(
    selectedId != null ? `reports:detail:${selectedId}` : '',
    () => api.report(selectedId as number),
    { enabled: selectedId != null },
  )

  const mine = mineRes.data ?? []
  const inbox = inboxRes.data ?? []
  const selectedReport = detailRes.data ?? null

  const listRes = role === 'mechanic' ? mineRes : inboxRes
  const listError = listRes.error
    ? mapApiError(listRes.error, ru.errors.load)
    : ''
  const listLoading = listRes.isRevalidating
  const showListSkeleton = listRes.isLoading && !listRes.data && !listError

  const detailError = detailRes.error
    ? mapApiError(detailRes.error, ru.errors.load)
    : ''
  const detailLoading = detailRes.isLoading && !selectedReport && !detailError

  function handleSelect(report: Report) {
    setSelectedId(report.id)
  }

  function handleCloseDetail() {
    setSelectedId(null)
  }

  async function refreshLists() {
    await Promise.all([
      role === 'mechanic' ? mineRes.refresh() : Promise.resolve(),
      inboxKey ? inboxRes.refresh() : Promise.resolve(),
    ])
  }

  async function handleDetailUpdated() {
    // Drop cached inbox/mine so /reports/inbox and /reports/mine reflect the
    // latest status without waiting for TTL.
    resourceStore.invalidate('reports:', { prefix: true })
    await refreshLists()
    refreshReportsBadge()
    if (selectedId != null) {
      try {
        await detailRes.refresh()
        const fresh = resourceStore.get<Report>(`reports:detail:${selectedId}`)
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

  const inboxTitle = isAdminRole(role) ? 'Эскалации' : 'Входящие'
  const inboxHint = isAdminRole(role)
    ? 'Открытые эскалации от операторов. Фильтр по парку — в верхней панели.'
    : 'Открытые репорты механиков по выбранному парку.'

  return (
    <div className="dashboard-page animate-in">
      <div className="dashboard-toolbar">
        <h1 className="dashboard-title">{ru.nav.reports}</h1>
        <button
          className="btn btn-secondary"
          disabled={listLoading || parksLoading || (isInboxRole(role) && parkId == null)}
          onClick={() => void refreshLists()}
          type="button"
        >
          {listLoading ? <Spinner label="Обновление" /> : 'Обновить'}
        </button>
      </div>

      {listError && <Alert tone="error">{listError}</Alert>}

      {role === 'mechanic' && (
        <>
          <Panel hint="Статусы ваших репортов и комментарии при возврате." title="Мои репорты">
            <ReportList
              emptyMessage="Вы ещё не создавали репортов."
              loading={showListSkeleton}
              reports={mine}
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
        </>
      )}

      {isInboxRole(role) && (
        <>
          {parkId == null && !parksLoading && (
            <EmptyBlock
              hint="Входящие репорты показываются по выбранному парку."
              icon="📥"
              title="Выберите парк в верхней панели"
            />
          )}

          {parkId != null && selectedId == null && (
            <Panel hint={inboxHint} title={inboxTitle}>
              <ReportList
                emptyMessage="Нет открытых репортов для выбранного парка."
                loading={showListSkeleton}
                onSelect={handleSelect}
                reports={inbox}
                selectedId={selectedId}
                showReturnComment={false}
              />
            </Panel>
          )}

          {parkId != null && selectedId != null && (
            <Panel title="Репорт">
              {detailError && <Alert tone="error">{detailError}</Alert>}
              {detailLoading && <SkeletonList rows={2} />}
              {!detailLoading && selectedReport && (
                <ReportDetail
                  canAct
                  onClose={handleCloseDetail}
                  onUpdated={() => void handleDetailUpdated()}
                  parkName={selectedPark?.name}
                  report={selectedReport}
                  showEscalate={role === 'operator'}
                />
              )}
            </Panel>
          )}
        </>
      )}

      {!role && !user && <SkeletonList rows={3} />}
    </div>
  )
}
