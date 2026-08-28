import type { TrackerAttachment, TrackerComment, TrackerIssueDetail } from '../../api'
import { ru } from '../../i18n/ru'
import {
  isImageAttachment,
  sortCommentsChronologically,
  splitPlatformComment,
} from './commentChat'
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
import { RobotCheckPanel } from './RobotCheckPanel'

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="issue-field">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  )
}

function CommentAttachmentImage({ attachment }: { attachment: TrackerAttachment }) {
  if (!attachment.url || !isImageAttachment(attachment)) {
    const size = formatFileSize(attachment.size)
    const label = size ? `${attachment.name} (${size})` : attachment.name
    return (
      <span className="issue-comment-file">
        {attachment.url ? (
          <a href={attachment.url} rel="noreferrer" target="_blank">
            {label}
          </a>
        ) : (
          label
        )}
      </span>
    )
  }

  return (
    <a
      className="issue-comment-image-link"
      href={attachment.url}
      rel="noreferrer"
      target="_blank"
      title={attachment.name}
    >
      <img alt={attachment.name} className="issue-comment-image" src={attachment.url} />
    </a>
  )
}

function CommentItem({
  comment,
  chatLayout = false,
}: {
  comment: TrackerComment
  chatLayout?: boolean
}) {
  const author = { display: comment.author ?? '', login: comment.author_login ?? '' }
  const { body, signature } = splitPlatformComment(comment.text)
  const attachments = comment.attachments ?? []
  const imageAttachments = attachments.filter(isImageAttachment)
  const otherAttachments = attachments.filter((item) => !isImageAttachment(item))

  return (
    <li className={`issue-comment${chatLayout ? ' issue-comment--chat' : ''}`}>
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
        {body && <p className="issue-comment-text">{body}</p>}
        {imageAttachments.length > 0 && (
          <div className="issue-comment-images">
            {imageAttachments.map((attachment) => (
              <CommentAttachmentImage attachment={attachment} key={attachment.id} />
            ))}
          </div>
        )}
        {otherAttachments.length > 0 && (
          <ul className="issue-comment-files">
            {otherAttachments.map((attachment) => (
              <li key={attachment.id}>
                <CommentAttachmentImage attachment={attachment} />
              </li>
            ))}
          </ul>
        )}
        {signature && <p className="issue-comment-signature">{signature}</p>}
      </div>
    </li>
  )
}

export function IssueDetailPanel({
  issue,
  comments,
  commentsAsHistory = false,
  loading,
}: {
  issue: TrackerIssueDetail | null
  comments: TrackerComment[]
  commentsAsHistory?: boolean
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
  const commentsTitle = commentsAsHistory ? ru.tracker.history : ru.tracker.comments
  const commentsEmpty = commentsAsHistory ? ru.tracker.historyEmpty : ru.tracker.commentsEmpty
  const sortedComments = sortCommentsChronologically(comments)
  const chatLayout = sortedComments.some(
    (comment) => (comment.attachments?.length ?? 0) > 0 || commentsAsHistory,
  )

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

      <RobotCheckPanel robot={issue.robot} />

      <section className={`issue-section${chatLayout ? ' issue-section--history' : ''}`}>
        <h3>
          {commentsTitle}
          {sortedComments.length > 0 && <span className="issue-count">{sortedComments.length}</span>}
        </h3>
        {sortedComments.length === 0 ? (
          <p className="issue-muted">{commentsEmpty}</p>
        ) : (
          <ul className="issue-comments">
            {sortedComments.map((comment) => (
              <CommentItem chatLayout={chatLayout} comment={comment} key={comment.id} />
            ))}
          </ul>
        )}
      </section>
    </article>
  )
}
