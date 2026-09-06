import { useLayoutEffect, useRef } from 'react'
import type { RobotCheckTab } from './robotCheckUrl'
export function RobotCheckTabs({ tabs, activeId, onChange }: { tabs: RobotCheckTab[]; activeId: string; onChange: (id: string) => void }) {
  const buttons = useRef<(HTMLButtonElement | null)[]>([])
  const container = useRef<HTMLDivElement | null>(null)
  useLayoutEffect(() => {
    let current = true
    const reveal = () => {
      if (!current) return
      const parent = container.current
      const selected = buttons.current.find(button => button?.getAttribute('aria-selected') === 'true')
      if (!parent || !selected) return
      const bounds = parent.getBoundingClientRect()
      const tab = selected.getBoundingClientRect()
      const rightInContent = tab.right - bounds.left + parent.scrollLeft
      parent.scrollLeft = Math.max(0, rightInContent - parent.clientWidth)
    }
    reveal()
    // Font swap can change all preceding tab widths after the first layout.
    void document.fonts?.ready.then(reveal)
    window.addEventListener('resize', reveal)
    return () => { current = false; window.removeEventListener('resize', reveal) }
  }, [activeId])
  return <div ref={container} className="rp-check-tabs" role="tablist" aria-label="Разделы проверки робота">
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
