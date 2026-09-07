import { api } from '../api'
import { AnalyticsWorkspace } from '../domains/analytics/AnalyticsWorkspace'
import type { AnalyticsApiClient } from '../domains/analytics/analyticsModel'

export function Analytics({ apiClient = api }: { apiClient?: AnalyticsApiClient }) {
  return <AnalyticsWorkspace apiClient={apiClient} />
}
