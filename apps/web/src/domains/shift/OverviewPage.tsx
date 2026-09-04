import { api } from '../../api'
import { InsightsPage } from '../insights/InsightsPage'
import type { OperationsApiClient } from '../insights/operations'

export function OverviewPage({ apiClient = api }: { apiClient?: OperationsApiClient }) {
  return <InsightsPage apiClient={apiClient} mode="overview" />
}
