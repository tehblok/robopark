import type { ReactElement, ReactNode } from 'react'
import type { InterfaceMode } from './interfaceModeStore'
import { ClassicShell } from './ClassicShell'
import { TaskFirstShell } from './TaskFirstShell'

export type PresentationShellSlots = {
  navigation: ReactNode
  header: ReactNode
  content: ReactNode
  context?: ReactNode
  action?: ReactNode
}

export function PresentationShell({ mode, slots, shellClassName, density, theme }: {
  mode: InterfaceMode
  slots: PresentationShellSlots
  shellClassName?: string
  density?: string
  theme?: string
}): ReactElement {
  // Keep this component type stable. Calling the two pure renderers here lets
  // React reconcile the shared slot positions instead of remounting live forms.
  return mode === 'task-first'
    ? TaskFirstShell({ slots, shellClassName, density, theme })
    : ClassicShell({ slots, shellClassName, density, theme })
}
