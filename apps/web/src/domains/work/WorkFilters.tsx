import { Button } from '../../design-system/actions/Button'
import { FormField } from '../../design-system/forms/FormField'
import type { WorkUrlState } from './workUrl'

const STATUS_CHOICES = [
  ['', 'Все открытые блокеры'],
  ['new', 'Новые'],
  ['moving', 'Перемещение'],
  ['queued', 'Очередь'],
  ['diagnostics', 'Диагностика'],
  ['waiting_team', 'Ожидает команду'],
  ['waiting_parts', 'Ожидает запчасти'],
  ['open', 'Открытые'],
  ['inProgress', 'В работе'],
  ['ready', 'Готовые'],
] as const

export type WorkFiltersProps = {
  value: WorkUrlState
  driver?: boolean
  loading: boolean
  onApply(next: WorkUrlState): void
}

export function WorkFilters({ value, driver = false, loading, onApply }: WorkFiltersProps) {
  const { filters } = value
  const choices = STATUS_CHOICES.filter(([status]) => !driver || ['', 'new', 'moving', 'open'].includes(status))
  const restrictions = [
    filters.robot ? `Робот: ${filters.robot}` : null,
    filters.assignee ? `Ответственный: ${filters.assignee}` : null,
    filters.ageHours ? `Старше ${filters.ageHours} ч` : null,
    filters.untagged ? 'Без тега парка' : null,
  ].filter(Boolean)

  return (
    <div className="rp-work-filters">
      <FormField id="work-status" label="Статус открытых блокеров">
        <select
          aria-busy={loading}
          onChange={(event) => {
            const next = { ...filters }
            if (event.target.value) next.status = event.target.value
            else delete next.status
            onApply({ filters: next, sort: 'oldest', page: 1 })
          }}
          value={filters.status ?? ''}
        >
          {choices.map(([status, label]) => <option key={status} value={status}>{label}</option>)}
          {filters.status && !choices.some(([status]) => status === filters.status) ? (
            <option value={filters.status}>Другой статус: {filters.status}</option>
          ) : null}
        </select>
      </FormField>
      <p className="rp-work-filters__ordering">От старых к новым</p>
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
