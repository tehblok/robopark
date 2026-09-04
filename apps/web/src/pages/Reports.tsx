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
  const [activePane, setActivePane] = useState<'mine' | 'inbox'>('mine')

  const selectedPark = parks.find((park) => park.id === parkId)

  const mineRes = useCachedResource<Report[]>(
    createEnabled ? `reports:${reportScope}:mine` : '',
    () => api.reportsMine(),
    { enabled: createEnabled && !parksLoading },
  )
  const inboxKey = inboxEnabled && parkId != null ? `reports:${reportScope}:inbox` : ''
  const inboxRes = useCachedResource<Report[]>(
    inboxKey,
    () => api.reportsInbox(parkId as number),
    { enabled: inboxEnabled && parkId != null && !parksLoading },
  )
  const detailRes = useCachedResource<Report>(
    selectedId != null ? `reports:${reportScope}:detail:${selectedId}` : '',
    () => api.report(selectedId as number),
    { enabled: selectedId != null },
  )

  const mine = mineRes.data ?? []
  const inbox = inboxRes.data ?? []
  const selectedReport = detailRes.data ?? null

  const listRes = createEnabled ? mineRes : inboxRes
  const listError = listRes.error ? mapApiError(listRes.error, ru.errors.load) : ''
  const listLoading = listRes.isRevalidating
  const showListSkeleton = listRes.isLoading && !listRes.data && !listError

  const detailError = detailRes.error ? mapApiError(detailRes.error, ru.errors.load) : ''
  const detailLoading = detailRes.isLoading && !selectedReport && !detailError

  function handleSelect(report: Report) {
    setSelectedId(report.id)
  }

  function handleCloseDetail() {
    setSelectedId(null)
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
            aria-selected={activePane === 'mine'}
            className={`btn btn-filter${activePane === 'mine' ? ' is-active' : ''}`}
            onClick={() => setActivePane('mine')}
            role="tab"
            type="button"
          >
            Мои репорты
          </button>
          <button
            aria-selected={activePane === 'inbox'}
            className={`btn btn-filter${activePane === 'inbox' ? ' is-active' : ''}`}
            onClick={() => setActivePane('inbox')}
            role="tab"
            type="button"
          >
            {inboxTitle}
          </button>
        </div>
      )}

      {listError && <Alert tone="error">{listError}</Alert>}

      {createEnabled && (
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

      {inboxEnabled && (
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
