import { describe, expect, it } from 'vitest'
import type { OpsJob } from '../../api'
import { updateProgress } from './opsPresentation'

const baseJob: OpsJob = {
  id: 'job-1', kind: 'update', state: 'running', phase: 'awaiting_host', log: '', error: null,
  artifact_ready: false, restart_required: false, created_at: '', updated_at: '',
  restore_phrase: 'ВОССТАНОВИТЬ', update_phrase: 'ОБНОВИТЬ', host_result: null,
  progress_phase: null, progress_percent: null,
}

describe('updateProgress', () => {
  it('presents forward host milestones with their server-projected percentages', () => {
    const milestones = [
      ['unpacking', 10, 'Распаковываем обновление'],
      ['building', 25, 'Собираем API и веб-интерфейс'],
      ['smoking', 45, 'Проверяем новую версию'],
      ['snapshotting', 60, 'Создаём резервную копию'],
      ['publishing', 70, 'Публикуем версию'],
      ['migrating', 82, 'Обновляем данные'],
      ['starting', 90, 'Запускаем сервисы'],
      ['health_check', 96, 'Проверяем работоспособность'],
    ] as const

    const values = milestones.map(([progress_phase, progress_percent, label]) => {
      const result = updateProgress({ ...baseJob, progress_phase, progress_percent })
      expect(result).toEqual({ percent: progress_percent, label, rollback: false })
      return result!.percent
    })

    expect(values).toEqual([10, 25, 45, 60, 70, 82, 90, 96])
  })

  it('labels rollback explicitly and rejects missing or malformed projections', () => {
    expect(updateProgress({ ...baseJob, progress_phase: 'rolling_back', progress_percent: 50 }))
      .toEqual({ percent: 50, label: 'Восстанавливаем предыдущую версию', rollback: true })
    expect(updateProgress(baseJob)).toBeNull()
    expect(updateProgress({ ...baseJob, progress_phase: 'validating', progress_percent: 5 })).toBeNull()
    expect(updateProgress({ ...baseJob, progress_phase: 'unknown', progress_percent: 70 })).toBeNull()
    expect(updateProgress({ ...baseJob, progress_phase: 'building', progress_percent: 101 })).toBeNull()
  })
})
