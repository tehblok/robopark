import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Report } from '../api'
import { useAuth } from '../auth-context'
import { ReportDetail } from '../components/reports/ReportDetail'
import { ReportForms } from '../components/reports/ReportForms'
import { ReportList } from '../components/reports/ReportList'
import { Alert, EmptyState, Panel } from '../components/PageShell'
import { mapApiError } from '../i18n/errors'
import { ru } from '../i18n/ru'
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

  const [mine, setMine] = useState<Report[]>([])
  const [inbox, setInbox] = useState<Report[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [selectedReport, setSelectedReport] = useState<Report | null>(null)
  const [listError, setListError] = useState('')
  const [detailError, setDetailError] = useState('')
  const [listLoading, setListLoading] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const requestIdRef = useRef(0)

  const selectedPark = parks.find((park) => park.id === parkId)

  const loadLists = useCallback(async () => {
    const requestId = ++requestIdRef.current
    setListLoading(true)
    setListError('')

    try {
      if (role === 'mechanic') {
        const data = await api.reportsMine()
        if (requestId !== requestIdRef.current) return
        setMine(data)
      } else if (isInboxRole(role)) {
        if (parkId == null) {
          if (requestId !== requestIdRef.current) return
          setInbox([])
        } else {
          const data = await api.reportsInbox(parkId)
          if (requestId !== requestIdRef.current) return
          setInbox(data)
        }
      }
    } catch (loadError) {
      if (requestId !== requestIdRef.current) return
      setListError(mapApiError(loadError, ru.errors.load))
      if (role === 'mechanic') setMine([])
      else setInbox([])
    } finally {
      if (requestId !== requestIdRef.current) return
      setListLoading(false)
    }
  }, [parkId, role])

  useEffect(() => {
    if (!user || parksLoading) return
    void loadLists()
  }, [loadLists, parksLoading, user])

  useEffect(() => {
    if (selectedId == null) {
      setSelectedReport(null)
      setDetailError('')
      return
    }

    let cancelled = false
    setDetailLoading(true)
    setDetailError('')

    api.report(selectedId)
      .then((report) => {
        if (!cancelled) setSelectedReport(report)
      })
      .catch((loadError) => {
        if (!cancelled) {
          setSelectedReport(null)
          setDetailError(mapApiError(loadError, ru.errors.load))
        }
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [selectedId])

  function handleSelect(report: Report) {
    setSelectedId(report.id)
  }

  function handleCloseDetail() {
    setSelectedId(null)
    setSelectedReport(null)
    setDetailError('')
  }

  async function handleDetailUpdated() {
    await loadLists()
    refreshReportsBadge()
    if (selectedId != null) {
      try {
        const fresh = await api.report(selectedId)
        setSelectedReport(fresh)
        if (fresh.status !== 'open') {
          handleCloseDetail()
        }
      } catch {
        handleCloseDetail()
      }
    }
  }

  async function handleCreated() {
    await loadLists()
    refreshReportsBadge()
  }

  const inboxTitle = isAdminRole(role) ? 'Эскалации' : 'Входящие'
  const inboxHint = isAdminRole(role)
    ? 'Открытые эскалации от операторов. Фильтр по парку — в верхней панели.'
    : 'Открытые репорты механиков по выбранному парку.'

  return (
    <div className="dashboard-page">
      <div className="dashboard-toolbar">
        <h1 className="dashboard-title">{ru.nav.reports}</h1>
        <button
          disabled={listLoading || parksLoading || (isInboxRole(role) && parkId == null)}
          onClick={() => void loadLists()}
          type="button"
        >
          Обновить
        </button>
      </div>

      {listError && <Alert tone="error">{listError}</Alert>}

      {role === 'mechanic' && (
        <>
          <Panel hint="Статусы ваших репортов и комментарии при возврате." title="Мои репорты">
            <ReportList
              emptyMessage="Вы ещё не создавали репортов."
              loading={listLoading}
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
              <EmptyState>Парк не назначен — обратитесь к администратору.</EmptyState>
            </Panel>
          )}
        </>
      )}

      {isInboxRole(role) && (
        <>
          {parkId == null && !parksLoading && (
            <Panel title={inboxTitle}>
              <EmptyState>Выберите парк в верхней панели, чтобы загрузить входящие.</EmptyState>
            </Panel>
          )}

          {parkId != null && selectedId == null && (
            <Panel hint={inboxHint} title={inboxTitle}>
              <ReportList
                emptyMessage="Нет открытых репортов для выбранного парка."
                loading={listLoading}
                onSelect={handleSelect}
                reports={inbox}
                showReturnComment={false}
              />
            </Panel>
          )}

          {parkId != null && selectedId != null && (
            <Panel title="Репорт">
              {detailError && <Alert tone="error">{detailError}</Alert>}
              {detailLoading && <EmptyState>{ru.loading}</EmptyState>}
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

      {!role && !user && (
        <Panel title={ru.nav.reports}>
          <EmptyState>{ru.loading}</EmptyState>
        </Panel>
      )}
    </div>
  )
}
