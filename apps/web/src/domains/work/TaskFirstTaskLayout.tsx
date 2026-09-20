import type { ReactNode } from 'react'

export function TaskFirstTaskLayout({ enabled, header, children, context, action, onActionHost }: {
  enabled: boolean
  header?: ReactNode
  children: ReactNode
  context?: ReactNode
  action?: ReactNode
  onActionHost?: (host: HTMLElement | null) => void
}) {
  return <div className={enabled ? 'a-task-layout' : 'classic-task-layout'}>
    <div className={enabled ? 'a-task-header' : 'classic-task-header'} data-task-header data-task-zone={enabled ? 'header' : undefined}>{header}</div>
    {enabled && context ? <aside className="a-task-context" data-task-zone="context" data-testid="task-context-zone">
      <div className="a-task-context__desktop">{context}</div>
      <details className="a-task-context__disclosure">
        <summary>Контекст задачи</summary>
        <div className="a-task-context__body">{context}</div>
      </details>
    </aside> : null}
    <section className={enabled ? 'a-task-workflow' : 'classic-task-workflow'} data-task-body data-task-zone={enabled ? 'workflow' : undefined} data-testid={enabled ? 'task-workflow-zone' : undefined}>{children}</section>
    {enabled && (action || onActionHost) ? <footer className="a-task-action" data-task-zone="action" data-testid="task-action-zone" ref={onActionHost}>{action}</footer> : null}
  </div>
}
