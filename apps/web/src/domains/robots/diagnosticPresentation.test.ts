import { expect, it } from 'vitest'
import type { DiagnosticEvent } from '../../api'
import { chooseAutomaticDiagnosticSelection, chooseAutomaticView } from './diagnosticPresentation'

const event: DiagnosticEvent = { id: 'rule:a', rule_id: 1, source_path: 'errors.0', source_segments: ['errors', 0], raw_value: 'LIDAR_OFFLINE', title: 'Лидар', description: 'Проверьте питание', severity: 'critical', sort_order: 4, part: 'Передний лидар', view: 'front', x: .25, y: .6, indicator: 'point' }
it('chooses severity before rule order, without mutating server events', () => {
  const events = Object.freeze([{ ...event, id: 'warning', severity: 'warning' as const, sort_order: 0, view: 'rear' as const }, event])
  expect(chooseAutomaticView(events)).toBe('front')
  expect(events[0].id).toBe('warning')
})
it('chooses the earlier rule and uses stable IDs for equal-order events across polls', () => {
  const first = { ...event, id: 'a', view: 'left' as const, sort_order: 1 }
  const second = { ...event, id: 'b', view: 'right' as const, sort_order: 1 }
  expect(chooseAutomaticView([event, second, first])).toBe('left')
  expect(chooseAutomaticView([first, second, event])).toBe('left')
})
it.each([
  { x: -1 }, { x: 1.1 }, { x: NaN }, { x: Infinity }, { x: null }, { y: -1 }, { y: 1.1 }, { y: NaN }, { y: null },
  { view: null }, { view: 'invalid' }, { indicator: null }, { indicator: 'invalid' }, { rule_id: null }, { part: null },
])('ignores an unlocalized or malformed marker %j without guessing its view', invalid => {
  expect(chooseAutomaticView([{ ...event, ...invalid } as DiagnosticEvent])).toBe('top')
  expect(chooseAutomaticView([{ ...event, ...invalid } as DiagnosticEvent, { ...event, severity: 'info', view: 'rear' }])).toBe('rear')
})
it('accepts coordinate boundaries and gives an empty event list a deterministic top view', () => {
  expect(chooseAutomaticView([{ ...event, x: 0, y: 1 }])).toBe('front')
  expect(chooseAutomaticView([])).toBe('top')
})
it('chooses a critical reading block and its configured view before warnings and normal values', () => {
  const readings = [
    { id: 1, section_id: 'wheels', state: 'warning' as const, view: 'front' as const },
    { id: 2, section_id: 'power', state: 'critical' as const, view: 'rear' as const },
    { id: 3, section_id: 'wheels', state: 'normal' as const, view: 'left' as const },
  ]
  expect(chooseAutomaticDiagnosticSelection([], readings, ['wheels', 'power'])).toEqual({ blockId: 'power', view: 'rear', eventId: null })
})
it('uses the leading error view and a relevant block with a reading on that view', () => {
  const readings = [
    { id: 1, section_id: 'power', state: 'normal' as const, view: 'rear' as const },
    { id: 2, section_id: 'wheels', state: 'normal' as const, view: 'front' as const },
  ]
  expect(chooseAutomaticDiagnosticSelection([event], readings, ['power', 'wheels'])).toEqual({ blockId: 'wheels', view: 'front', eventId: event.id })
})
