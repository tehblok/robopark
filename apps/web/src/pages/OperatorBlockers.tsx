import { useState } from 'react'
import { api, type Park } from '../api'
import { useAuth } from '../auth-context'
import { Alert, PageShell } from '../components/PageShell'
import { RequestParkModal } from '../components/parks/RequestParkModal'
import { IssueDrawer } from '../components/tracker/IssueDrawer'
import { TaskFilterBar, TaskList } from '../components/tracker/TaskBoard'
import { EmptyBlock, SkeletonList } from '../components/ui/Feedback'
import { mapApiError } from '../i18n/errors'
import { ru } from '../i18n/ru'
import { useCachedResource } from '../lib/resource'
import { useParkContext } from '../park-context'

export function OperatorBlockers() {
  const { refreshUser } = useAuth()
  const { parkId, parks, parksLoading } = useParkContext()
  const [status, setStatus] = useState('all')
  const [openKey, setOpenKey] = useState('')
  const [parkModalOpen, setParkModalOpen] = useState(false)

  const availableRes = useCachedResource<Park[]>('operator:available-parks', () => api.availableParks())
  const canRequestPark = (availableRes.data ?? []).length > 0

  const blockersKey = parkId == null ? '' : `operator:blockers:${parkId}:${status}`
  const blockersRes = useCachedResource(
    blockersKey,
    () => api.operatorBlockers(parkId as number, status),
    { enabled: parkId != null },
  )

  const items = blockersRes.data?.items ?? []
  const counts = blockersRes.data?.counts ?? {}
  const parkTag = blockersRes.data?.park_tag ?? ''

  const errorText = blockersRes.error
    ? mapApiError(blockersRes.error, ru.errors.tasks)
    : ''

  const handleParkSubmitted = () => {
    void refreshUser()
    void availableRes.refresh()
  }

  const requestParkButton = (
    <button
      className="btn btn-secondary"
      disabled={availableRes.isLoading && !availableRes.data}
      onClick={() => setParkModalOpen(true)}
      title={canRequestPark ? undefined : ru.parks.requestParkEmptyHint}
      type="button"
    >
      {ru.parks.requestPark}
    </button>
  )

  if (openKey) {
    return (
      <>
        <PageShell
          actions={requestParkButton}
          subtitle={`Парк ${parkTag || '…'}`}
          title="Задача"
        >
          <IssueDrawer
            canWrite
            issueKey={openKey}
            onChanged={() => {
              void blockersRes.refresh()
            }}
            onClose={() => setOpenKey('')}
          />
        </PageShell>
        <RequestParkModal
          onClose={() => setParkModalOpen(false)}
          onSubmitted={handleParkSubmitted}
          open={parkModalOpen}
        />
      </>
    )
  }

  const showColdSkeleton =
    parkId != null && blockersRes.isLoading && !blockersRes.data && !errorText

  return (
    <>
      <PageShell
        actions={requestParkButton}
        subtitle={`Блокеры парка ${parkTag || '…'} · старые сверху`}
        title="Блокеры"
      >
        {errorText && <Alert tone="error">{errorText}</Alert>}

        {!parks.length && !errorText && !parksLoading && (
          <EmptyBlock
            action={
              <button className="btn" onClick={() => setParkModalOpen(true)} type="button">
                {ru.parks.requestPark}
              </button>
            }
            hint={ru.parks.noAssignedParksHint}
            icon="🏭"
            title={ru.parks.noAssignedParks}
          />
        )}

        {parks.length > 0 && (
          <>
            <TaskFilterBar counts={counts} onChange={setStatus} value={status} />

            {showColdSkeleton && <SkeletonList rows={4} />}
            {!showColdSkeleton && !items.length && !errorText && blockersRes.data && (
              <EmptyBlock
                hint="Смените фильтр статуса или обновите список позже."
                icon="📋"
                title="Нет открытых блокеров для выбранного фильтра"
              />
            )}
            {items.length > 0 && (
              <TaskList items={items} onSelect={setOpenKey} selected={openKey} />
            )}
          </>
        )}
      </PageShell>
      <RequestParkModal
        onClose={() => setParkModalOpen(false)}
        onSubmitted={handleParkSubmitted}
        open={parkModalOpen}
      />
    </>
  )
}
