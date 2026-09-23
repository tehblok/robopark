import type { ReactNode } from 'react'

/** Stable parent keeps document commands mounted with the Classic action layout. */
export function InventorySecondaryActions({ children }: { children: ReactNode }) {
  return <details className="inventory-secondary-actions" open>
    <summary>Другие действия</summary>
    {children}
  </details>
}
