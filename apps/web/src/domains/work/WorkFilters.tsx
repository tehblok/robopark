import { Button } from '../../design-system/actions/Button'
import type { WorkUrlState } from './workUrl'

export type WorkFiltersProps = {
  value: WorkUrlState
  driver?: boolean
  manager?: boolean
  loading: boolean
  onApply(next: WorkUrlState): void
}

export function WorkFilters({ value, driver = false, manager = false, loading, onApply }: WorkFiltersProps) {
  const { filters } = value
  void driver
  void loading
  const restrictions = [
    filters.robot ? `Робот: ${filters.robot}` : null,
    filters.assignee ? `Ответственный: ${filters.assignee}` : null,
    filters.ageHours ? `Старше ${filters.ageHours} ч` : null,
    filters.untagged ? 'Без тега парка' : null,
  ].filter(Boolean)

  return (
    <div className="rp-work-filters">
      <p className="rp-work-filters__ordering">
        {filters.status === 'queued' || !filters.status ? 'В очереди' : `Статус из ссылки: ${filters.status}`}
      </p>
      <p className="rp-work-filters__ordering">От старых к новым</p>
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
