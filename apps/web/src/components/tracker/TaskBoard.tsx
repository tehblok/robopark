import type { Blocker } from '../../api'
import { ru, taskFilterLabel } from '../../i18n/ru'
import {
  TASK_FILTERS,
  formatAge,
  initials,
  isStale,
  personName,
  priorityLabel,
  priorityTone,
  statusTone,
} from './issue-utils'

export function TaskFilterBar({
  counts,
  value,
  onChange,
}: {
  counts: Record<string, number>
  value: string
  onChange: (next: string) => void
}) {
  return (
    <div className="task-filters">
      {TASK_FILTERS.map((filter) => (
        <button
          className={`btn btn-filter${value === filter ? ' is-active' : ''}`}
          key={filter}
          onClick={() => onChange(filter)}
          type="button"
        >
          {taskFilterLabel(filter)}
          <span className="task-filter-count">{counts[filter] ?? 0}</span>
        </button>
      ))}
    </div>
  )
}

/**
 * Task list shared by the mechanic and operator views. Rows mirror the Tracker
 * issue list so both screens look and behave the same.
 */
export function TaskList({
  items,
  selected,
  onSelect,
}: {
  items: Blocker[]
  selected?: string
  onSelect: (key: string) => void
}) {
  return (
    <ul className="issue-list">
      {items.map((item) => {
        const priority = priorityLabel(item.priority)
        const age = formatAge(item.hours_created)
        const isSelected = item.key === selected
        return (
          <li key={item.key}>
            <button
              aria-current={isSelected}
              className={`issue-row${isSelected ? ' is-selected' : ''}`}
              onClick={() => onSelect(item.key)}
              type="button"
            >
              <span className={`issue-priority-bar tone-${priorityTone(item.priority)}`} />
              <span className="issue-row-body">
                <span className="issue-row-top">
                  <span className="issue-key">{item.key}</span>
                  <span className={`issue-status tone-${statusTone(item)}`}>
                    {item.status}
                  </span>
                </span>

                <span className="issue-summary">{item.summary}</span>

                <span className="issue-row-meta">
                  {item.robot && (
                    <span className="issue-chip issue-chip--robot">{item.robot}</span>
                  )}
                  {priority && <span className="issue-chip">{priority}</span>}
                  <span className="issue-chip">{taskFilterLabel(item.bucket)}</span>
                  {age && (
                    <span
                      className={`issue-chip${isStale(item.hours_created) ? ' is-stale' : ''}`}
                      title={ru.tracker.fields.age}
                    >
                      {age}
                    </span>
                  )}
                  <span
                    className="issue-assignee"
                    title={`${ru.tracker.fields.assignee}: ${personName(item.assignee)}`}
                  >
                    <span
                      aria-hidden="true"
                      className={`issue-avatar${item.assignee ? '' : ' is-empty'}`}
                    >
                      {initials(item.assignee)}
                    </span>
                  </span>
                </span>
              </span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}
