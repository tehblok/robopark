import type { ReactNode } from 'react'
import { useInterfaceMode } from '../../app/interface/InterfaceModeProvider'

/** Stable parent: choosing a design does not remount document commands. */
export function InventorySecondaryActions({ children }: { children: ReactNode }) {
  const { mode } = useInterfaceMode()
  return <details className="inventory-secondary-actions" open={mode === 'classic' ? true : undefined}>
    <summary>Другие действия</summary>
    {children}
  </details>
}
