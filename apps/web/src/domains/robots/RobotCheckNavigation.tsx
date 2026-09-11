import { useId, useLayoutEffect, useRef, useState } from 'react'
import { RobotCheckTabs } from './RobotCheckTabs'
import type { RobotCheckTab } from './robotCheckUrl'

// oxlint-disable-next-line react/only-export-components -- public navigation contract belongs with its consumer.
export const PRIMARY_CHECK_TAB_IDS = ['state', 'errors', 'scheme'] as const
type PrimaryCheckTabId = typeof PRIMARY_CHECK_TAB_IDS[number]

function isPrimary(id: string): id is PrimaryCheckTabId {
  return PRIMARY_CHECK_TAB_IDS.includes(id as PrimaryCheckTabId)
}

export function RobotCheckNavigation({ tabs, activeId, onChange }: {
  tabs: RobotCheckTab[]
  activeId: string
  onChange: (id: string) => void
}) {
  const [open, setOpen] = useState(false)
  const menuId = useId()
  const trigger = useRef<HTMLButtonElement | null>(null)
  const firstItem = useRef<HTMLButtonElement | null>(null)
  const menuItems = useRef<(HTMLButtonElement | null)[]>([])
  const pendingFocus = useRef<string | null>(null)
  const primary = tabs.filter(tab => isPrimary(tab.id))
  const secondary = tabs.filter(tab => !isPrimary(tab.id))
  const activeSecondary = secondary.find(tab => tab.id === activeId)
  const visible = activeSecondary ? [...primary, activeSecondary] : primary

  useLayoutEffect(() => {
    if (open) firstItem.current?.focus()
  }, [open])
  useLayoutEffect(() => {
    if (pendingFocus.current !== activeId) return
    document.getElementById(`robot-check-tab-${activeId}`)?.focus()
    pendingFocus.current = null
  }, [activeId])

  return <div className="rp-check-navigation">
    <RobotCheckTabs tabs={visible} activeId={activeId} onChange={onChange} />
    {secondary.length ? <div className="rp-check-more" onBlur={event => {
      if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false)
    }}>
      <button type="button" ref={trigger} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? menuId : undefined}
        onClick={() => setOpen(value => !value)} onKeyDown={event => {
          if (event.key === 'ArrowDown') { event.preventDefault(); setOpen(true) }
        }}>Ещё</button>
      {open ? <div className="rp-check-more-menu" id={menuId} role="menu" aria-label="Другие разделы" onKeyDown={event => {
        if (event.key === 'Escape') { event.preventDefault(); setOpen(false); trigger.current?.focus() }
        const current = menuItems.current.indexOf(document.activeElement as HTMLButtonElement)
        const destination = event.key === 'Home' ? 0 : event.key === 'End' ? secondary.length - 1
          : event.key === 'ArrowDown' ? (current + 1) % secondary.length
            : event.key === 'ArrowUp' ? (current + secondary.length - 1) % secondary.length : null
        if (destination !== null) { event.preventDefault(); menuItems.current[destination]?.focus() }
      }}>
        {secondary.map((tab, index) => <button key={tab.id} type="button" role="menuitem" ref={node => { menuItems.current[index] = node; if (index === 0) firstItem.current = node }}
          onClick={() => { pendingFocus.current = tab.id; setOpen(false); onChange(tab.id) }}>{tab.title}</button>)}
      </div> : null}
    </div> : null}
  </div>
}
