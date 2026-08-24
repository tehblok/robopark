import type { TrackerIssue } from '../../api'

export function IssueList({
  items,
  selected,
  onSelect,
}: {
  items: TrackerIssue[]
  selected: string
  onSelect: (key: string) => void
}) {
  return (
    <ul className="tracker-list">
      {items.map((item) => (
        <li key={item.key}>
          <button
            className={item.key === selected ? 'tracker-item selected' : 'tracker-item'}
            onClick={() => onSelect(item.key)}
            type="button"
          >
            <strong>{item.key}</strong> <span>{item.summary}</span> <small>{item.status}</small>
          </button>
        </li>
      ))}
    </ul>
  )
}
