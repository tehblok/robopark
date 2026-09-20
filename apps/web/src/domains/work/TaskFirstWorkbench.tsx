import { Tabs } from '../../design-system/navigation/Tabs'

export type WorkSection = 'task' | 'open' | 'closed' | 'check'

export function TaskFirstWorkbench({ enabled, activeTab, focus, canCheck, onChange }: {
  enabled: boolean; activeTab: WorkSection; focus: 'repair' | 'chat'; canCheck: boolean
  onChange(tab: WorkSection | 'chat'): void
}) {
  const workflowItems = enabled ? [
    { id: 'task', label: 'Ремонт' },
    ...(canCheck ? [{ id: 'check', label: 'Проверка' }] : []),
    { id: 'chat', label: 'Чат' },
  ] : [
    { id: 'task', label: 'Задача' }, ...(canCheck ? [{ id: 'check', label: 'Проверка робота' }] : []),
  ]
  const relatedItems = [
    { id: 'open', label: 'Открытые задачи' },
    { id: 'closed', label: 'Закрытые задачи' },
  ]
  return <div className="rp-work-sections">
    <Tabs ariaLabel="Разделы задачи" value={enabled && activeTab === 'task' && focus === 'chat' ? 'chat' : activeTab}
      items={workflowItems} onChange={tab => onChange(tab as WorkSection | 'chat')}
      panelIdFor={tab => `work-panel-${tab === 'chat' ? 'task' : tab}`} />
    <div className="a-work-related">
      <Tabs ariaLabel="Другие задачи робота" value={activeTab} items={relatedItems}
        onChange={tab => onChange(tab as WorkSection)} panelIdFor={tab => `work-panel-${tab}`} />
    </div>
  </div>
}
