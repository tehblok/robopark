import { Tabs } from '../../design-system/navigation/Tabs'

export type WorkSection = 'task' | 'open' | 'closed' | 'check'

export function TaskFirstWorkbench({ enabled, activeTab, focus, canCheck, onChange }: {
  enabled: boolean; activeTab: WorkSection; focus: 'repair' | 'chat'; canCheck: boolean
  onChange(tab: WorkSection | 'chat'): void
}) {
  const items = enabled ? [
    { id: 'task', label: 'Ремонт' },
    ...(canCheck ? [{ id: 'check', label: 'Проверка' }] : []),
    { id: 'chat', label: 'Чат' },
  ] : [
    { id: 'task', label: 'Задача' }, { id: 'open', label: 'Открытые задачи' },
    { id: 'closed', label: 'Закрытые задачи' }, ...(canCheck ? [{ id: 'check', label: 'Проверка робота' }] : []),
  ]
  return <div className="rp-work-sections">
    <Tabs ariaLabel="Разделы задачи" value={enabled && activeTab === 'task' && focus === 'chat' ? 'chat' : activeTab}
      items={items} onChange={tab => onChange(tab as WorkSection | 'chat')}
      panelIdFor={tab => `work-panel-${tab === 'chat' ? 'task' : tab}`} />
    {enabled ? <nav className="a-work-related" aria-label="Другие задачи робота">
      <button id="tab-open" type="button" aria-pressed={activeTab === 'open'} onClick={() => onChange('open')}>Открытые задачи</button>
      <button id="tab-closed" type="button" aria-pressed={activeTab === 'closed'} onClick={() => onChange('closed')}>Закрытые задачи</button>
    </nav> : null}
  </div>
}
