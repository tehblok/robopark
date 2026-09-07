import { useEffect, useRef, useState } from 'react'
import { Button } from '../../design-system/actions/Button'
import { summarizeIssueDescription } from './issueDescription'
import { IssueRichText } from './IssueRichText'
import type { TrackerAttachment, TrackerComment, TrackerIssueDetail } from '../../api'
import { Link } from 'react-router-dom'
import { safeHttpUrl } from '../../lib/safeUrl'
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
  const safeUrl = safeHttpUrl(attachment.url)
  if (!safeUrl || !isImageAttachment(attachment)) {
    const size = formatFileSize(attachment.size)
    const label = size ? `${attachment.name} (${size})` : attachment.name
    return (
      <span className="issue-comment-file">
        {safeUrl ? (
          <a href={safeUrl} rel="noreferrer" target="_blank">
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
      href={safeUrl}
      rel="noreferrer"
      target="_blank"
      title={attachment.name}
    >
      <img alt={attachment.name} className="issue-comment-image" src={safeUrl} />
    </a>
  )
}

function CommentItem({
  comment,
  chatLayout = false,
  isNew = false,
}: {
  comment: TrackerComment
  chatLayout?: boolean
  isNew?: boolean
}) {
  const author = { display: comment.author ?? '', login: comment.author_login ?? '' }
  const { body, signature } = splitPlatformComment(comment.text)
  const attachments = comment.attachments ?? []
  const imageAttachments = attachments.filter(isImageAttachment)
  const otherAttachments = attachments.filter((item) => !isImageAttachment(item))

  return (
    <li data-comment-id={comment.id} tabIndex={-1} className={`issue-comment${chatLayout ? ' issue-comment--chat' : ''}${isNew ? ' issue-comment--new' : ''}`}>
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
        {body && <div className="issue-comment-text"><IssueRichText text={body} /></div>}
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

function IssueDetailPanelContent({
  issue,
  comments,
  commentsAsHistory = false,
  commentsLoading = false,
  currentUser,
  accountKey,
  showRobotCheck = true,
  onOpenRobotCheck,
  loading,
}: {
  issue: TrackerIssueDetail | null
  comments: TrackerComment[]
  commentsAsHistory?: boolean
  commentsLoading?: boolean
  currentUser?: string
  accountKey?: string
  showRobotCheck?: boolean
  onOpenRobotCheck?: () => void
  loading?: boolean
}) {
  const articleRef = useRef<HTMLElement>(null)
  const baseline = useRef<{ issue: string | null; comments: Set<string> | null }>({ issue: null, comments: null })
  const [newCommentIds, setNewCommentIds] = useState<string[]>([])
  const [issueChanged, setIssueChanged] = useState(false)
  const fingerprint = issue ? JSON.stringify([
    issue.summary, issue.status, issue.assignee?.login ?? '', issue.assignee?.display ?? '', issue.description ?? '',
  ]) : null

  useEffect(() => {
    if (!issue || loading) return
    if (baseline.current.issue !== null && baseline.current.issue !== fingerprint) setIssueChanged(true)
    baseline.current.issue = fingerprint
    if (commentsLoading) return
    const previous = baseline.current.comments
    if (previous) {
      const incoming = comments.filter(comment => !previous.has(comment.id)
        && !(comment.author_login && [currentUser, accountKey].includes(comment.author_login)))
      if (incoming.length) setNewCommentIds(ids => [...new Set([...ids, ...incoming.map(comment => comment.id)])])
    }
    baseline.current.comments = new Set([...(previous ?? []), ...comments.map(comment => comment.id)])
  }, [issue, fingerprint, loading, comments, commentsLoading, currentUser, accountKey])

  const jumpToNew = () => {
    const target = Array.from(articleRef.current?.querySelectorAll<HTMLElement>('[data-comment-id]') ?? [])
      .find(element => newCommentIds.includes(element.dataset.commentId ?? ''))
    target?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    target?.focus({ preventScroll: true })
    setNewCommentIds([])
  }

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
  const description = summarizeIssueDescription(issue.description ?? '')
  const issueUrl = safeHttpUrl(issue.url)
  const robotReference = issue.robot?.trim()

  return (
    <article className="issue-detail" ref={articleRef}>
      <header className="issue-detail-head">
        <div className="issue-detail-title-row">
          {issueUrl ? (
            <a
              className="issue-detail-key"
              href={issueUrl}
              rel="noreferrer"
              target="_blank"
              title={ru.tracker.actions.openInTracker}
            >
              {issue.key}
            </a>
          ) : (
            <span className="issue-detail-key">{issue.key}</span>
          )}
          <span className={`issue-status tone-${statusTone(issue)}`}>{issue.status}</span>
          {priority && (
            <span className={`issue-badge tone-${priorityTone(issue.priority)}`}>
              {priority}
            </span>
          )}
        </div>
        <h2 className="issue-detail-summary">{issue.summary}</h2>
      </header>

      {(issueChanged || newCommentIds.length > 0) && <div className="issue-update-notice" role="status">
        {issueChanged && <span>Задача обновлена</span>}
        {newCommentIds.length > 0 && <Button type="button" variant="secondary" onClick={jumpToNew}>
          {newCommentIds.length} {newCommentIds.length % 10 === 1 && newCommentIds.length % 100 !== 11
            ? 'новый комментарий' : newCommentIds.length % 10 >= 2 && newCommentIds.length % 10 <= 4
              && !(newCommentIds.length % 100 >= 12 && newCommentIds.length % 100 <= 14)
              ? 'новых комментария' : 'новых комментариев'} — перейти
        </Button>}
        <Button type="button" variant="ghost" onClick={() => { setIssueChanged(false); setNewCommentIds([]) }}>Скрыть уведомление</Button>
      </div>}

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
        {robotReference && (
          <Field label={ru.tracker.fields.robot}>
            {onOpenRobotCheck ? <button className="rp-work-robot-link" type="button" onClick={onOpenRobotCheck}
              aria-label={`${ru.tracker.robotCheck.open} ${robotReference}`}>{robotReference}</button> : <Link
              aria-label={`${ru.tracker.robotCheck.open} ${robotReference}`}
              className="rp-work-robot-link"
              to={`/robots/${encodeURIComponent(robotReference)}/check`}
            >
              {robotReference}
            </Link>}
          </Field>
        )}
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
              const fileUrl = safeHttpUrl(attachment.url)
              return (
                <li className="issue-attachment" key={attachment.id}>
                  {fileUrl ? (
                    <a href={fileUrl} rel="noreferrer" target="_blank">
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
        {description.trim() ? (
          <IssueRichText text={description} collapsible />
        ) : (
          <p className="issue-muted">{ru.tracker.descriptionEmpty}</p>
        )}
      </section>

      {showRobotCheck ? <RobotCheckPanel robot={issue.robot} /> : null}

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
              <CommentItem chatLayout={chatLayout} comment={comment} isNew={newCommentIds.includes(comment.id)} key={comment.id} />
            ))}
          </ul>
        )}
      </section>
    </article>
  )
}

export function IssueDetailPanel(props: React.ComponentProps<typeof IssueDetailPanelContent>) {
  return <IssueDetailPanelContent {...props} key={JSON.stringify([props.accountKey ?? props.currentUser, props.issue?.key])} />
}
