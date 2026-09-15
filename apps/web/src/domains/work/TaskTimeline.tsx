import type { TaskTimelineItem, TrackerAttachment } from '../../api'
import { IssueRichText } from '../../components/tracker/IssueRichText'
import { formatDateTime } from '../../components/tracker/issue-utils'
import { safeHttpUrl } from '../../lib/safeUrl'
import { TaskSyncStatus } from './TaskSyncStatus'

function Attachment({ attachment }: { attachment: TrackerAttachment }) {
  const url = safeHttpUrl(attachment.url)
  return url
    ? <a href={url} rel="noreferrer" target="_blank">{attachment.name}</a>
    : <span>{attachment.name}</span>
}

export function TaskTimeline({ items }: { items: readonly TaskTimelineItem[] }) {
  const sorted = [...items].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id))
  return <section aria-label="Чат задачи">
    {sorted.length === 0 ? <p className="issue-muted">В чате пока нет сообщений.</p> : <ol className="issue-comments">
      {sorted.map(item => <li className={`issue-comment issue-comment--chat task-message task-message--${item.kind}`} key={item.id}>
        <div className="issue-comment-body">
          <div className="issue-comment-head"><strong>{item.author}</strong><time>{formatDateTime(item.created_at)}</time></div>
          <div className="issue-comment-text"><IssueRichText text={item.text} /></div>
          {item.attachments.length > 0 ? <div className="issue-attachments">
            {item.attachments.map(attachment => <div key={attachment.id}><Attachment attachment={attachment} /></div>)}
          </div> : null}
          <TaskSyncStatus state={item.sync_state} />
        </div>
      </li>)}
    </ol>}
  </section>
}
