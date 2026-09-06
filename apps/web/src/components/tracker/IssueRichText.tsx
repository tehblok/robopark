import { useId, useState } from 'react'
import Markdown from 'react-markdown'
import { Button } from '../../design-system/actions/Button'
import './task-card.css'

export function IssueRichText({ text, collapsible = false }: { text: string; collapsible?: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const id = useId()
  const isLong = collapsible && (text.length > 1000 || text.split('\n').length > 16)
  const content = isLong && !expanded ? `${text.slice(0, 700).split('\n').slice(0, 10).join('\n')}…` : text
  return <>
    <div className="issue-rich-text" id={id}>
      <Markdown components={{
        a: ({ children, href }) => href
          ? <a href={href} rel="noreferrer noopener" target="_blank">{children}</a>
          : <span>{children}</span>,
        // Inline remote images are represented as text; attachments have their own preview.
        img: ({ alt }) => <span>{alt}</span>,
      }}>{content}</Markdown>
    </div>
    {isLong && <Button className="issue-description-toggle" variant="ghost" type="button"
      aria-expanded={expanded} aria-controls={id} onClick={() => setExpanded(!expanded)}>
      {expanded ? 'Свернуть описание' : 'Показать описание полностью'}
    </Button>}
  </>
}
