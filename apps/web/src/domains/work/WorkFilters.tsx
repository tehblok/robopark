import { type FormEvent, useState } from 'react'
import { Button } from '../../design-system/actions/Button'
import { FormField } from '../../design-system/forms/FormField'
import type { WorkUrlState } from './workUrl'

const STATUS_CHOICES = [
  ['', 'Все незавершённые'],
  ['new', 'Новые'],
  ['moving', 'Перемещение'],
  ['queued', 'Очередь'],
  ['diagnostics', 'Диагностика'],
  ['waiting_team', 'Ожидает команду'],
  ['waiting_parts', 'Ожидает запчасти'],
  ['open', 'Открытые'],
  ['inProgress', 'В работе'],
  ['ready', 'Готовые'],
  ['resolved', 'Решённые'],
  ['closed', 'Закрытые'],
] as const

export type WorkFiltersProps = {
  value: WorkUrlState
  trackerLogin?: string | null
  allowUntagged: boolean
  loading: boolean
  onApply(next: WorkUrlState): void
}

export function WorkFilters({
  value,
  trackerLogin,
  allowUntagged,
  loading,
  onApply,
}: WorkFiltersProps) {
  const [draft, setDraft] = useState(value)

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const ageHours = draft.filters.ageHours
    onApply({
      filters: {
        ...(draft.filters.queue?.trim() ? { queue: draft.filters.queue.trim() } : {}),
        ...(draft.filters.status?.trim() ? { status: draft.filters.status.trim() } : {}),
        ...(draft.filters.robot?.trim() ? { robot: draft.filters.robot.trim() } : {}),
        ...(draft.filters.assignee?.trim()
          ? { assignee: draft.filters.assignee.trim() }
          : {}),
        ...(allowUntagged && draft.filters.untagged ? { untagged: true } : {}),
        ...(ageHours != null && Number.isSafeInteger(ageHours) && ageHours > 0
          ? { ageHours }
          : {}),
      },
      sort: 'oldest',
      page: 1,
    })
  }

  return (
    <form
      className="rp-work-filters"
      onReset={() => setDraft(value)}
      onSubmit={submit}
    >
      <FormField id="work-queue" label="Очередь" required>
        <input
          onChange={(event) => setDraft({
            ...draft,
            filters: { ...draft.filters, queue: event.target.value },
          })}
          value={draft.filters.queue ?? ''}
        />
      </FormField>
      <FormField id="work-status" label="Статус">
        <select
          onChange={(event) => setDraft({
            ...draft,
            filters: { ...draft.filters, status: event.target.value },
          })}
          value={draft.filters.status ?? ''}
        >
          {STATUS_CHOICES.map(([status, label]) => <option key={status} value={status}>{label}</option>)}
          {draft.filters.status && !STATUS_CHOICES.some(([status]) => status === draft.filters.status) ? (
            <option value={draft.filters.status}>Другой статус: {draft.filters.status}</option>
          ) : null}
        </select>
      </FormField>
      <FormField id="work-robot" label="Робот">
        <input
          onChange={(event) => setDraft({
            ...draft,
            filters: { ...draft.filters, robot: event.target.value },
          })}
          value={draft.filters.robot ?? ''}
        />
      </FormField>
      <FormField
        hint={trackerLogin ? `Мой логин: ${trackerLogin}` : undefined}
        id="work-assignee"
        label="Ответственный"
      >
        <input
          onChange={(event) => setDraft({
            ...draft,
            filters: { ...draft.filters, assignee: event.target.value },
          })}
          value={draft.filters.assignee ?? ''}
        />
      </FormField>
      <FormField id="work-age" label="Старше, часов">
        <input
          inputMode="numeric"
          min="1"
          onChange={(event) => setDraft({
            ...draft,
            filters: {
              ...draft.filters,
              ageHours: event.target.value ? Number(event.target.value) : undefined,
            },
          })}
          type="number"
          value={draft.filters.ageHours ?? ''}
        />
      </FormField>
      <p className="rp-work-filters__ordering">Сначала старые</p>
      {allowUntagged ? (
        <label className="rp-work-filters__checkbox">
          <input
            checked={Boolean(draft.filters.untagged)}
            onChange={(event) => setDraft({
              ...draft,
              filters: { ...draft.filters, untagged: event.target.checked },
            })}
            type="checkbox"
          />
          <span>Без тега парка</span>
        </label>
      ) : null}
      <div className="rp-work-filters__actions">
        <Button disabled={loading} type="reset" variant="secondary">
          Сбросить фильтры
        </Button>
        <Button busy={loading} leadingIcon="filter" type="submit">
          Применить фильтры
        </Button>
      </div>
    </form>
  )
}
