import type { TrackerComment, TrackerIssueDetail } from '../../api'

export function IssueDetailPanel({
  issue,
  comments,
}: {
  issue: TrackerIssueDetail | null
  comments: TrackerComment[]
}) {
  if (!issue) return <p>Select issue.</p>
  return (
    <article className="tracker-panel">
      <h3>{issue.key}</h3>
      <p>{issue.summary}</p>
      <p>Status: {issue.status}</p>
      <p>Queue: {issue.queue ?? '-'}</p>
      <h4>Comments</h4>
      <ul>
        {comments.map((comment) => (
          <li key={comment.id}><strong>{comment.author ?? 'user'}:</strong> {comment.text}</li>
        ))}
      </ul>
    </article>
  )
}
