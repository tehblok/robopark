import { describe, expect, it } from 'vitest'
import { maintenanceKindLabel, shouldShowMaintenance } from './maintenanceLogic'

describe('shouldShowMaintenance', () => {
  it('hides the overlay for the royal who started the job', () => {
    expect(shouldShowMaintenance({ active: true, kind: 'update', operator: true })).toBe(false)
  })

  it('shows the overlay for everyone else while a job runs', () => {
    expect(shouldShowMaintenance({ active: true, kind: 'snapshot', operator: false })).toBe(true)
  })

  it('hides when idle', () => {
    expect(shouldShowMaintenance({ active: false, kind: null, operator: false })).toBe(false)
    expect(shouldShowMaintenance(null)).toBe(false)
  })
})

describe('maintenanceKindLabel', () => {
  const labels = { snapshot: 'Снимок', restore: 'Восстановление', update: 'Обновление' }
  it('maps known kinds', () => {
    expect(maintenanceKindLabel('snapshot', labels, 'x')).toBe('Снимок')
  })
  it('falls back', () => {
    expect(maintenanceKindLabel(null, labels, 'работы')).toBe('работы')
  })
})
