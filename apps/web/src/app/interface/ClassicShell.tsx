import type { ReactElement } from 'react'
import type { PresentationShellSlots } from './PresentationShell'
import './ClassicShell.css'

export function ClassicShell({ slots, shellClassName = '', density, theme }: {
  slots: PresentationShellSlots
  shellClassName?: string
  density?: string
  theme?: string
}): ReactElement {
  return (
    <div className={`rp-app-shell rp-classic-shell ${shellClassName}`.trim()} data-density={density} data-theme={theme} data-testid="classic-shell">
      <div className="rp-shell__navigation-zone rp-classic-shell__navigation" data-shell-zone="navigation">{slots.navigation}</div>
      <div className="rp-shell__main-column rp-classic-shell__workspace">
        <header className="rp-shell__topbar rp-classic-shell__header" data-shell-zone="header">{slots.header}</header>
        <main className="rp-shell__content rp-classic-shell__content" data-shell-zone="content" id="main-content" tabIndex={-1}>
          {slots.content}
        </main>
      </div>
    </div>
  )
}
