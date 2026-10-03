import type { EmergencySnapshot } from '../../api'
import { ROBOT_OBSERVATION_FUTURE_TOLERANCE_MS } from './robotDetailModel'

export type RobotReturnAssessment = {
  state: 'checks_passed' | 'attention' | 'unknown'
  reasons: string[]
}

/** Client-side decision aid. Only the operator can accept the robot and close its task. */
export function assessRobotReturn(
  snapshot: EmergencySnapshot,
  { browserOnline, failed = false, now = new Date() }: { browserOnline: boolean; failed?: boolean; now?: Date },
): RobotReturnAssessment {
  const observed = Date.parse(snapshot.observed_at)
  if (!browserOnline || failed || snapshot.stale || snapshot.online !== true
    || !Number.isFinite(observed) || observed > now.getTime() + ROBOT_OBSERVATION_FUTURE_TOLERANCE_MS
    || now.getTime() - observed > 300_000) {
    return { state: 'unknown', reasons: ['Нет актуальной проверки робота'] }
  }

  const attention: string[] = []
  const unknown: string[] = []
  for (const index of [1, 2] as const) {
    const connected = snapshot[`battery${index}_connected`]
    const percent = snapshot[`battery${index}_percent`]
    if (connected === false) attention.push(`АКБ ${index} не подключена`)
    else if (connected !== true || percent == null || !Number.isFinite(percent) || percent < 0 || percent > 100) unknown.push(`Нет данных об АКБ ${index}`)
    else if (percent < 90) attention.push(`АКБ ${index} ниже 90%`)
  }
  const events = snapshot.diagnostic_events
  if (events == null) unknown.push('Нет данных о диагностических ошибках')
  else {
    const marked = events.filter(event => event.rule_id != null).length
    const unmarked = events.length - marked
    if (marked) attention.push(`Есть размеченные ошибки: ${marked}`)
    if (unmarked) unknown.push(`Неразмеченные ошибки: ${unmarked}`)
  }
  if (snapshot.error_banner || snapshot.wheels_fault.length) unknown.push('Есть другие сообщения о неисправности')
  const reasons = [...attention, ...unknown]
  return { state: attention.length ? 'attention' : unknown.length ? 'unknown' : 'checks_passed', reasons }
}
