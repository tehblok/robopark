import type { ReactNode } from 'react'

export function TaskFirstTaskLayout({ enabled, children, context, action }: {
  enabled: boolean
  children: ReactNode
  context?: ReactNode
  action?: ReactNode
}) {
  return <div className={enabled ? 'a-task-layout' : 'classic-task-layout'}>
    <section className={enabled ? 'a-task-workflow' : 'classic-task-workflow'} data-task-zone={enabled ? 'workflow' : undefined} data-testid={enabled ? 'task-workflow-zone' : undefined}>{children}</section>
    {enabled && context ? <aside className="a-task-context" data-task-zone="context" data-testid="task-context-zone">
      <div className="a-task-context__desktop">{context}</div>
      <details className="a-task-context__disclosure">
        <summary>Контекст задачи</summary>
        <div className="a-task-context__body">{context}</div>
      </details>
    </aside> : null}
    {enabled && action ? <footer className="a-task-action" data-task-zone="action" data-testid="task-action-zone">{action}</footer> : null}
  </div>
}
