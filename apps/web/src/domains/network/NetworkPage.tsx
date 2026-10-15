import type { AgentPoolStatus as AgentStatus, ComputePoolStatus as ComputeStatus } from '../../api'
import { api } from '../../api'
import { PageShell } from '../../components/PageShell'
import { AgentPoolStatus } from '../../pwa/AgentPoolStatus'
import { ComputePoolStatus } from '../../pwa/ComputePoolStatus'
import './network.css'

export function NetworkPage({
  loadAgents = api.agentPoolStatus,
  loadCompute = api.computePoolStatus,
}: {
  loadAgents?: () => Promise<AgentStatus>
  loadCompute?: () => Promise<ComputeStatus>
}) {
  return <PageShell
    title="Сеть Mac"
    subtitle="Сервер обрабатывает работу первым. Доверенные Mac подключаются как резерв при сбоях и для недоступных серверу ресурсов."
  >
    <div className="rp-network-page">
      <ComputePoolStatus load={loadCompute} />
      <AgentPoolStatus load={loadAgents} />
    </div>
  </PageShell>
}
