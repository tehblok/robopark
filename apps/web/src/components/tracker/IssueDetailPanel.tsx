import type { TrackerComment, TrackerIssueDetail } from '../../api'
import { ru } from '../../i18n/ru'
import {
  formatAge,
  formatDateTime,
  formatFileSize,
  initials,
  isStale,
  personName,
  priorityLabel,
  priorityTone,
  statusTone,
} from './issue-utils'

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="issue-field">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  )
}

function CommentItem({ comment }: { comment: TrackerComment }) {
  const author = { display: comment.author ?? '', login: comment.author_login ?? '' }
  return (
    <li className="issue-comment">
      <span className="issue-avatar issue-avatar--comment" aria-hidden="true">
        {initials(author)}
      </span>
      <div className="issue-comment-body">
        <div className="issue-comment-head">
          <span className="issue-comment-author">
            {comment.author?.trim() || ru.tracker.fields.nobody}
          </span>
          <time className="issue-comment-date">{formatDateTime(comment.created_at)}</time>
        </div>
        <p className="issue-comment-text">{comment.text}</p>
      </div>
    </li>
  )
}

export function IssueDetailPanel({
  issue,
  comments,
  loading,
}: {
  issue: TrackerIssueDetail | null
  comments: TrackerComment[]
  loading?: boolean
}) {
  if (loading) {
    return <p className="issue-detail-empty">{ru.loading}</p>
  }

  if (!issue) {
    return <p className="issue-detail-empty">{ru.tracker.selectHint}</p>
  }

  const priority = priorityLabel(issue.priority)
  const age = formatAge(issue.hours_created)

  return (
    <article className="issue-detail">
      <header className="issue-detail-head">
        <div className="issue-detail-title-row">
          <a
            className="issue-detail-key"
            href={issue.url}
            rel="noreferrer"
            target="_blank"
            title={ru.tracker.actions.openInTracker}
          >
            {issue.key}
          </a>
          <span className={`issue-status tone-${statusTone(issue)}`}>{issue.status}</span>
          {priority && (
            <span className={`issue-badge tone-${priorityTone(issue.priority)}`}>
              {priority}
            </span>
          )}
        </div>
        <h2 className="issue-detail-summary">{issue.summary}</h2>
      </header>

      <dl className="issue-fields">
        <Field label={ru.tracker.fields.assignee}>
          <span className="issue-person">
            <span className={`issue-avatar${issue.assignee ? '' : ' is-empty'}`} aria-hidden="true">
              {initials(issue.assignee)}
            </span>
            {personName(issue.assignee)}
          </span>
        </Field>
        <Field label={ru.tracker.fields.reporter}>{personName(issue.reporter)}</Field>
        {issue.type && <Field label={ru.tracker.fields.type}>{issue.type}</Field>}
        <Field label={ru.tracker.fields.queue}>
          {issue.queue || ru.tracker.fields.empty}
        </Field>
        {issue.robot && <Field label={ru.tracker.fields.robot}>{issue.robot}</Field>}
        <Field label={ru.tracker.fields.created}>
          {formatDateTime(issue.created_at)}
          {age && (
            <span className={`issue-age${isStale(issue.hours_created) ? ' is-stale' : ''}`}>
              {age}
            </span>
          )}
        </Field>
        {issue.updated_at && (
          <Field label={ru.tracker.fields.updated}>{formatDateTime(issue.updated_at)}</Field>
        )}
        {issue.resolution && (
          <Field label={ru.tracker.fields.resolution}>{issue.resolution}</Field>
        )}
        {issue.tags && issue.tags.length > 0 && (
          <Field label={ru.tracker.fields.tags}>
            <span className="issue-tag-row">
              {issue.tags.map((tag) => (
                <span className="issue-chip" key={tag}>
                  {tag}
                </span>
              ))}
            </span>
          </Field>
        )}
        {issue.components && issue.components.length > 0 && (
          <Field label={ru.tracker.fields.components}>
            <span className="issue-tag-row">
              {issue.components.map((item) => (
                <span className="issue-chip" key={item}>
                  {item}
                </span>
              ))}
            </span>
          </Field>
        )}
      </dl>

      {issue.attachments && issue.attachments.length > 0 && (
        <section className="issue-section">
          <h3>
            {ru.tracker.attachments}
            <span className="issue-count">{issue.attachments.length}</span>
          </h3>
          <ul className="issue-attachments">
            {issue.attachments.map((attachment) => {
              const size = formatFileSize(attachment.size)
              const label = size ? `${attachment.name} (${size})` : attachment.name
              return (
                <li className="issue-attachment" key={attachment.id}>
                  {attachment.url ? (
                    <a href={attachment.url} rel="noreferrer" target="_blank">
                      {label}
                    </a>
                  ) : (
                    <span>{label}</span>
                  )}
                </li>
              )
            })}
          </ul>
        </section>
      )}

      <section className="issue-section">
        <h3>{ru.tracker.description}</h3>
        {issue.description?.trim() ? (
          <p className="issue-description">{issue.description}</p>
        ) : (
          <p className="issue-muted">{ru.tracker.descriptionEmpty}</p>
        )}
      </section>

      <section className="issue-section">
        <h3>
          {ru.tracker.comments}
          {comments.length > 0 && <span className="issue-count">{comments.length}</span>}
        </h3>
        {comments.length === 0 ? (
          <p className="issue-muted">{ru.tracker.commentsEmpty}</p>
        ) : (
          <ul className="issue-comments">
            {comments.map((comment) => (
              <CommentItem comment={comment} key={comment.id} />
            ))}
          </ul>
        )}
      </section>
    </article>
  )
}
