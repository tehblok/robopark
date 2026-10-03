import { Button } from '../../design-system/actions/Button'
import { trackerStatusLabel } from '../../components/tracker/trackerStatusLabel'
import type { WorkUrlState } from './workUrl'

const viewingStatuses = [
  { key: 'all', label: 'Все открытые' },
  { key: 'queued', label: 'В очереди' },
  { key: 'new', label: 'Новые' },
  { key: 'moving', label: 'Перемещение' },
  { key: 'diagnostics', label: 'Диагностика' },
  { key: 'waiting_team', label: 'Ждём смежников' },
  { key: 'waiting_parts', label: 'Ожидание поставки' },
] as const

export type WorkFiltersProps = {
  value: WorkUrlState
  driver?: boolean
  queueFirst?: boolean
  manager?: boolean
  loading: boolean
  onApply(next: WorkUrlState): void
}

export function WorkFilters({ value, driver = false, queueFirst = false, manager = false, loading, onApply }: WorkFiltersProps) {
  const { filters } = value
  const statuses = driver ? viewingStatuses.filter(({ key }) => key === 'all' || key === 'new' || key === 'moving') : viewingStatuses
  const selectedStatus = filters.status ?? 'all'
  const linkedStatus = !driver && !statuses.some(({ key }) => key === selectedStatus) ? selectedStatus : null
  const restrictions = [
    filters.robot ? `Робот: ${filters.robot}` : null,
    filters.assignee ? `Ответственный: ${filters.assignee}` : null,
    filters.ageHours ? `Старше ${filters.ageHours} ч` : null,
    filters.untagged ? 'Без тега парка' : null,
  ].filter(Boolean)

  return (
    <div className="rp-work-filters">
      <label className="rp-work-filters__status">Статус задач
        <select disabled={loading} onChange={(event) => onApply({
          ...value,
          filters: { ...filters, status: event.target.value === 'all' ? undefined : event.target.value },
          page: 1,
        })} value={selectedStatus}>
          {statuses.map(({ key, label }) => <option key={key} value={key}>{label}</option>)}
          {linkedStatus ? <option value={linkedStatus}>{trackerStatusLabel(linkedStatus)}</option> : null}
        </select>
      </label>
      <p className="rp-work-filters__ordering">{queueFirst && !filters.status ? 'Сначала очередь · затем остальные по возрасту' : 'От старых к новым'}</p>
      {manager ? <label className="checkbox-field">
        <input checked={Boolean(filters.includeHidden)} disabled={loading} onChange={(event) => onApply({
          ...value,
          filters: { ...filters, includeHidden: event.target.checked || undefined },
          page: 1,
        })} type="checkbox" />
        <span>Показать скрытые задачи</span>
      </label> : null}
      {restrictions.length ? <div className="rp-work-filters__context" aria-label="Ограничения из ссылки" role="group">
        {restrictions.map(label => <span key={label}>{label}</span>)}
        <Button onClick={() => onApply({
          filters: {
            ...(filters.queue ? { queue: filters.queue } : {}),
            ...(filters.status ? { status: filters.status } : {}),
          },
          sort: 'oldest',
          page: 1,
        })} variant="secondary">Сбросить ограничения</Button>
      </div> : null}
    </div>
  )
}
