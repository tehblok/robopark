import type { ReactElement, ReactNode } from 'react'
import type { InterfaceMode } from './interfaceModeStore'
import { ClassicShell } from './ClassicShell'

export type PresentationShellSlots = {
  navigation: ReactNode
  header: ReactNode
  content: ReactNode
  context?: ReactNode
  action?: ReactNode
}

export function PresentationShell({ mode: _mode, slots, shellClassName, density, theme }: {
  mode: InterfaceMode
  slots: PresentationShellSlots
  shellClassName?: string
  density?: string
  theme?: string
}): ReactElement {
  return ClassicShell({ slots, shellClassName, density, theme })
}
