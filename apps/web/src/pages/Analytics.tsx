import { api } from '../api'
import { InsightsPage } from '../domains/insights/InsightsPage'
import type { OperationsApiClient } from '../domains/insights/operations'

export function Analytics({ apiClient = api }: { apiClient?: OperationsApiClient }) {
  return <InsightsPage apiClient={apiClient} mode="analytics" />
}
