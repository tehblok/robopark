import { useEffect, useState } from 'react'
import { api, type Park } from '../api'
import { Alert, PageShell, Panel } from '../components/PageShell'
import { RequestParkModal } from '../components/parks/RequestParkModal'
import { IssueDrawer } from '../components/tracker/IssueDrawer'
import { TaskFilterBar, TaskList } from '../components/tracker/TaskBoard'
import { EmptyBlock, SkeletonList } from '../components/ui/Feedback'
import { mapApiError } from '../i18n/errors'
import { ru } from '../i18n/ru'
import { useCachedResource } from '../lib/resource'

export function OperatorBlockers() {
  const [parkId, setParkId] = useState<number | null>(null)
  const [status, setStatus] = useState('all')
  const [openKey, setOpenKey] = useState('')
  const [parkModalOpen, setParkModalOpen] = useState(false)

  const parksRes = useCachedResource<Park[]>('operator:parks', () => api.operatorParks())
  const availableRes = useCachedResource<Park[]>('operator:available-parks', () => api.availableParks())
  const parks = parksRes.data ?? []
  const canRequestPark = (availableRes.data ?? []).length > 0

  useEffect(() => {
    if (parkId == null && parks.length > 0) {
      setParkId(parks[0].id)
    }
  }, [parkId, parks])

  const blockersKey = parkId == null ? '' : `operator:blockers:${parkId}:${status}`
  const blockersRes = useCachedResource(
    blockersKey,
    () => api.operatorBlockers(parkId as number, status),
    { enabled: parkId != null },
  )

  const items = blockersRes.data?.items ?? []
  const counts = blockersRes.data?.counts ?? {}
  const parkTag = blockersRes.data?.park_tag ?? ''

  const loadError = blockersRes.error ?? parksRes.error
  const errorText = loadError
    ? mapApiError(loadError, blockersRes.error ? ru.errors.tasks : ru.errors.load)
    : ''

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
          onSubmitted={() => {
            void Promise.all([parksRes.refresh(), availableRes.refresh()])
          }}
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

        {parks.length > 1 && (
          <Panel hint="Задачи показываются по выбранному парку." title="Парк">
            <select
              aria-label="Парк"
              className="park-select"
              onChange={(event) => setParkId(Number(event.target.value))}
              value={parkId ?? ''}
            >
              {parks.map((park) => (
                <option key={park.id} value={park.id}>
                  {park.name} ({park.tag})
                </option>
              ))}
            </select>
          </Panel>
        )}

        {!parks.length && !errorText && !parksRes.isLoading && (
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
        onSubmitted={() => {
          void Promise.all([parksRes.refresh(), availableRes.refresh()])
        }}
        open={parkModalOpen}
      />
    </>
  )
}
