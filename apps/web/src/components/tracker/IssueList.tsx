import type { TrackerIssue } from '../../api'
import { ru } from '../../i18n/ru'
import {
  formatAge,
  initials,
  isStale,
  personName,
  priorityLabel,
  priorityTone,
  statusTone,
} from './issue-utils'

export function IssueList({
  items,
  selected,
  onSelect,
  loading,
  total,
  hasMore,
  onLoadMore,
}: {
  items: TrackerIssue[]
  selected: string
  onSelect: (key: string) => void
  loading?: boolean
  total?: number
  hasMore?: boolean
  onLoadMore?: () => void
}) {
  if (loading && !items.length) {
    return <p className="issue-list-empty">{ru.loading}</p>
  }

  if (!items.length) {
    return <p className="issue-list-empty">{ru.tracker.listEmpty}</p>
  }

  return (
    <div className="issue-list-wrap">
      <div className="issue-list-count">
        {ru.tracker.shown} {items.length}
        {typeof total === 'number' && total > items.length
          ? ` ${ru.tracker.of} ${total}`
          : ''}
      </div>

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
                        className={`issue-avatar${item.assignee ? '' : ' is-empty'}`}
                        aria-hidden="true"
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

      {hasMore && onLoadMore && (
        <button
          className="btn btn-secondary issue-list-more"
          disabled={loading}
          onClick={onLoadMore}
          type="button"
        >
          {loading ? ru.loading : ru.tracker.showMore}
        </button>
      )}
    </div>
  )
}
