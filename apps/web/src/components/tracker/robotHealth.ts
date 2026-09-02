import { api, type EmergencySnapshot } from '../../api'
import { ru } from '../../i18n/ru'

export function robotCheckPathForRobot(robot: string): string {
  const query = robot.trim()
  return `/robots/${encodeURIComponent(query)}/check`
}

export function criticalFindings(snapshot: EmergencySnapshot): string[] {
  const findings: string[] = []
  if (snapshot.online === false) {
    findings.push(ru.emergency.offlineBanner)
  }
  if (snapshot.error_banner) {
    findings.push(snapshot.error_banner)
  }
  if (snapshot.wheels_fault.length > 0) {
    const slots = snapshot.wheels_fault
      .map((slot) => {
        const labels = ru.emergency.wheelSlots as Record<string, string>
        return labels[slot] ?? slot
      })
      .join(', ')
    findings.push(`${ru.tracker.robotCheck.wheelsFault}: ${slots}`)
  }
  return findings
}

export async function inspectRobotHealth(robot: string): Promise<string[]> {
  const query = robot.trim()
  const resolved = await api.emergencyResolve(query)
  const snapshot = await api.emergencySnapshot(resolved.vin)
  return criticalFindings(snapshot)
}
