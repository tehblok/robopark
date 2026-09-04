import { useRef } from 'react'
import type { RobotCheckTab } from './robotCheckUrl'
export function RobotCheckTabs({ tabs, activeId, onChange }: { tabs: RobotCheckTab[]; activeId: string; onChange: (id: string) => void }) {
  const buttons = useRef<(HTMLButtonElement | null)[]>([])
  return <div className="rp-check-tabs" role="tablist" aria-label="Разделы проверки робота">
    {tabs.map((tab, index) => <button key={tab.id} type="button" role="tab"
      id={`robot-check-tab-${tab.id}`} aria-controls={`robot-check-panel-${tab.id}`}
      aria-selected={activeId === tab.id} tabIndex={activeId === tab.id ? 0 : -1}
      ref={node => { buttons.current[index] = node }} onClick={() => onChange(tab.id)}
      onKeyDown={event => {
        const destination = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
          : event.key === 'ArrowRight' ? (index + 1) % tabs.length
            : event.key === 'ArrowLeft' ? (index + tabs.length - 1) % tabs.length : null
        if (destination === null) return
        event.preventDefault(); buttons.current[destination]?.focus(); onChange(tabs[destination].id)
      }}>{tab.title}</button>)}
  </div>
}
