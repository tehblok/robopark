import { describe, expect, it } from 'vitest'

import type { DefectCode, TaskRepairOptions } from '../../api'
import { suggestRepairFields, type RepairTextSource } from './repairPrefill'

const options: TaskRepairOptions = {
  issue_key: 'ROBOPARK-1',
  components: [
    { id: 'wheel', label: 'Мотор-колесо', tracker_name: 'ROBOT_SUSPENSION_WHEEL', aliases: ['колесо'] },
    { id: 'front-left', label: 'Переднее левое мотор-колесо', tracker_name: 'ROBOT_SUSPENSION_WHEELS_FRONT_LEFT' },
    { id: 'front-right', label: 'Переднее правое мотор-колесо', tracker_name: 'ROBOT_SUSPENSION_WHEELS_FRONT_RIGHT' },
    { id: 'camera', label: 'Камера', tracker_name: 'ROBOT_SENSORS_CAMERA' },
    { id: 'camera-wire', label: 'Кабель камеры', tracker_name: 'ROBOT_SENSORS_CAMERA_WIRE', aliases: ['провод камеры'] },
    { id: 'battery', label: 'Аккумулятор', tracker_name: 'ROBOT_BATTERY', aliases: ['АКБ'] },
  ],
  selected_component_ids: [], suggested_component_ids: [], suggestion_reason: null,
  defect_code: null, solution_method: null,
  solution_methods: [
    { code: 'CHANGE', label: 'Замена' },
    { code: 'REPAIR', label: 'Ремонт' },
    { code: 'DIAG', label: 'Диагностика' },
  ],
  field_snapshot: { component_ids: [], defect_code: null, solution_method: null },
}

const defects: DefectCode[] = [
  { code: 'EL-02', label: 'Обрыв электроцепи', description: null },
  { code: 'CH-03', label: 'Износ компонента', description: null },
]

function suggest(...sources: RepairTextSource[]) {
  return suggestRepairFields({ options, defectCodes: defects, sources })
}

describe('suggestRepairFields', () => {
  it('prefers the most specific positional component over a generic alias', () => {
    const result = suggest({ kind: 'title', text: 'Не крутится переднее левое мотор-колесо', label: 'Заголовок' })

    expect(result).toHaveLength(1)
    expect(result[0].componentIds).toEqual(['front-left'])
  })

  it('distinguishes a camera cable from the camera sensor', () => {
    const result = suggest({ kind: 'error', text: 'Обнаружен обрыв кабеля камеры, EL-02', label: 'Ошибка' })

    expect(result[0]).toMatchObject({ componentIds: ['camera-wire'], defectCode: 'EL-02' })
  })

  it('accepts an exact broad alias only as an explicit suggestion card', () => {
    expect(suggest({ kind: 'description', text: 'АКБ', label: 'Описание' })[0].componentIds).toEqual(['battery'])
  })

  it('suggests performed action only from an affirmative draft', () => {
    const result = suggest({ kind: 'draft', text: 'Заменил кабель камеры', label: 'Черновик', allowAction: true })

    expect(result[0]).toMatchObject({ componentIds: ['camera-wire'], solutionMethod: 'CHANGE' })
  })

  it('recognizes an affirmative passive repair with ё', () => {
    expect(suggest({ kind: 'draft', text: 'Кабель камеры заменён', allowAction: true })[0])
      .toMatchObject({ componentIds: ['camera-wire'], solutionMethod: 'CHANGE' })
  })

  it.each([
    { kind: 'title' as const, text: 'Заменить камеру', allowAction: true },
    { kind: 'comment' as const, text: 'Заменил камеру', allowAction: false },
    { kind: 'draft' as const, text: 'Не заменил камеру', allowAction: true },
    { kind: 'draft' as const, text: 'Планирую заменить камеру', allowAction: true },
    { kind: 'draft' as const, text: 'В отчёте написано: «заменил камеру»', allowAction: true },
    { kind: 'draft' as const, text: 'Выполненные работы: заменил камеру', allowAction: true },
    { kind: 'draft' as const, text: 'Заменил бы камеру', allowAction: true },
    { kind: 'draft' as const, text: 'Если заменил камеру, нужна проверка', allowAction: true },
    { kind: 'draft' as const, text: 'Неизвестно заменил ли камеру', allowAction: true },
    { kind: 'draft' as const, text: '`Заменил камеру`', allowAction: true },
    { kind: 'draft' as const, text: '```text\nЗаменил камеру\n```', allowAction: true },
    { kind: 'draft' as const, text: 'Выполненные работы:\nЗаменил камеру', allowAction: true },
    { kind: 'draft' as const, text: '«Предыдущий отчёт. Заменил камеру»', allowAction: true },
  ])('does not infer performed work from $kind: $text', source => {
    expect(suggest({ ...source, label: 'Контекст' })[0]?.solutionMethod).toBeUndefined()
  })

  it('keeps conflicting components in separate cards', () => {
    const result = suggest(
      { kind: 'title', text: 'Камера не работает', label: 'Заголовок' },
      { kind: 'description', text: 'Проверить переднее левое мотор-колесо', label: 'Описание' },
    )

    expect(result.map(item => item.componentIds)).toEqual(expect.arrayContaining([['camera'], ['front-left']]))
  })

  it('bounds suggestions and excerpts', () => {
    const result = suggest(
      { kind: 'title', text: 'Камера не работает', label: 'Заголовок' },
      { kind: 'description', text: 'Проверить переднее левое мотор-колесо', label: 'Описание' },
      { kind: 'error', text: 'EL-02', label: 'Ошибка' },
      { kind: 'draft', text: 'Заменил АКБ', label: 'Черновик', allowAction: true },
    )

    expect(result).toHaveLength(3)
    expect(result.every(item => item.evidence.every(entry => entry.excerpt.length <= 160))).toBe(true)
  })

  it('merges non-conflicting evidence only for the same exact component', () => {
    const result = suggest(
      { kind: 'title', text: 'Кабель камеры', label: 'Заголовок' },
      { kind: 'error', text: 'Кабель камеры: EL-02', label: 'Ошибка' },
    )

    expect(result).toHaveLength(1)
    expect(result[0]).toMatchObject({ componentIds: ['camera-wire'], defectCode: 'EL-02' })
    expect(result[0].evidence).toEqual([
      { source: 'Заголовок', excerpt: 'Кабель камеры' },
      { source: 'Ошибка', excerpt: 'Кабель камеры: EL-02' },
    ])
  })

  it('keeps performed actions scoped to their own clause', () => {
    const result = suggest({
      kind: 'draft',
      text: 'Заменил кабель камеры. Флаг не менял',
      label: 'Черновик',
      allowAction: true,
    })

    expect(result).toContainEqual(expect.objectContaining({
      componentIds: ['camera-wire'],
      solutionMethod: 'CHANGE',
    }))
    expect(result.filter(item => item.solutionMethod === 'CHANGE')).toHaveLength(1)
  })

  it('uses a specific multi-word alias instead of a generic sensor name', () => {
    const result = suggest({
      kind: 'draft',
      text: 'Заменил провод камеры WH-05',
      label: 'Черновик',
      allowAction: true,
    })

    expect(result[0]).toMatchObject({ componentIds: ['camera-wire'], solutionMethod: 'CHANGE' })
  })

  it('abstains from choosing one of two equally explicit components', () => {
    const result = suggest({
      kind: 'draft',
      text: 'Заменил камеру и аккумулятор',
      label: 'Черновик',
      allowAction: true,
    })

    expect(result.every(item => item.componentIds === undefined && item.solutionMethod === undefined)).toBe(true)
  })

  it('abstains when a healthy sensor and damaged cable are separate mentions', () => {
    const result = suggest({
      kind: 'description',
      text: 'Камеры исправны, поврежден провод камеры',
      label: 'Описание',
    })

    expect(result[0]?.componentIds).toBeUndefined()
  })

  it.each([
    '> Заменил камеру',
    'Если бы заменил камеру',
    'Хотел, чтобы заменил камеру',
    'Заменил камеру?',
  ])('does not treat quoted, conditional, or questioned text as performed work: %s', text => {
    const result = suggest({ kind: 'draft', text, label: 'Черновик', allowAction: true })

    expect(result[0]?.solutionMethod).toBeUndefined()
  })

  it.each(['EL-99', 'Это не EL-02', 'EL-02 не подтвердился'])('does not assert an unknown or negated defect: %s', text => {
    const result = suggest({ kind: 'error', text, label: 'Ошибка' })

    expect(result[0]?.defectCode).toBeUndefined()
  })

  it.each([
    'Заменил кабель камеры и аккумулятор',
    'Заменил переднее левое мотор-колесо и аккумулятор',
  ])('abstains when specific but non-overlapping parts share one action: %s', text => {
    const result = suggest({ kind: 'draft', text, label: 'Черновик', allowAction: true })

    expect(result.every(item => item.componentIds === undefined && item.solutionMethod === undefined)).toBe(true)
  })

  it('suppresses a generic sensor only when its mention overlaps a specific cable', () => {
    const result = suggest({ kind: 'draft', text: 'Заменил провод камеры', label: 'Черновик', allowAction: true })

    expect(result[0]).toMatchObject({ componentIds: ['camera-wire'], solutionMethod: 'CHANGE' })
  })

  it('keeps an incomplete position on the generic wheel instead of guessing a side', () => {
    const result = suggest({ kind: 'description', text: 'Левое колесо', label: 'Описание' })

    expect(result[0].componentIds).toEqual(['wheel'])
  })
})
