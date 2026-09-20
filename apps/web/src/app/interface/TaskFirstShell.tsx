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
    <div className={`app-shell rp-app-shell rp-task-first-shell ${shellClassName}`.trim()} data-density={density} data-theme={theme} data-testid="task-first-shell">
      <div className="rp-shell__navigation-zone" data-shell-zone="navigation">{slots.navigation}</div>
      <div className="app-main rp-shell__main-column">
        <header className="rp-shell__topbar" data-shell-zone="header">{slots.header}</header>
        <main className="app-content rp-shell__content" data-shell-zone="content" id="main-content" tabIndex={-1}>
          {slots.content}
        </main>
        <aside className="rp-shell__presentation-context" data-shell-zone="context" hidden={!slots.context}>{slots.context}</aside>
        <div className="rp-shell__presentation-action" data-shell-zone="action" hidden={!slots.action}>{slots.action}</div>
      </div>
    </div>
  )
}
