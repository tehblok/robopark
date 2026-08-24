export const REPORTS_BADGE_REFRESH = 'robopark:reports-badge-refresh'

export function refreshReportsBadge(): void {
  window.dispatchEvent(new Event(REPORTS_BADGE_REFRESH))
}
