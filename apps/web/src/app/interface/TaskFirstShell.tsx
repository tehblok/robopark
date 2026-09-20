import type { ReactElement } from 'react'
import type { PresentationShellSlots } from './PresentationShell'
import './TaskFirstShell.css'

export function TaskFirstShell({ slots, shellClassName = '', density, theme }: {
  slots: PresentationShellSlots
  shellClassName?: string
  density?: string
  theme?: string
}): ReactElement {
  return (
    <div className={`rp-app-shell rp-task-first-shell ${shellClassName}`.trim()} data-density={density} data-theme={theme} data-testid="task-first-shell">
      <div className="rp-shell__navigation-zone rp-task-first-shell__navigation" data-shell-zone="navigation">{slots.navigation}</div>
      <div className="rp-shell__main-column rp-task-first-shell__workspace">
        <header className="rp-shell__topbar rp-task-first-shell__header" data-shell-zone="header">{slots.header}</header>
        <main className="rp-shell__content rp-task-first-shell__content" data-shell-zone="content" id="main-content" tabIndex={-1}>
          {slots.content}
        </main>
        <aside className="rp-shell__presentation-context rp-task-first-shell__context" data-shell-zone="context" hidden={!slots.context}>{slots.context}</aside>
        <div className="rp-shell__presentation-action rp-task-first-shell__action" data-shell-zone="action" hidden={!slots.action}>{slots.action}</div>
      </div>
    </div>
  )
}
