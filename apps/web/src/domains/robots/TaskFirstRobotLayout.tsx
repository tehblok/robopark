import type { ReactNode } from 'react'

// Stable parents preserve the diagram portal, selected markers and live data.
export function TaskFirstRobotLayout({ identity, detail }: { identity: ReactNode; detail: ReactNode }) {
  return <div className="rp-check-layout a-robot-layout">
    <div className="rp-check-layout__overview">{identity}</div>
    <div className="rp-check-layout__details">{detail}</div>
  </div>
}
