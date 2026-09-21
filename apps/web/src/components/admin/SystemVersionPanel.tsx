import type { ReleaseStatus } from '../../api'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { Panel } from '../PageShell'

const supportText = {
  supported: 'Поддерживается', ending: 'Поддержка скоро завершится',
  expired: 'Поддержка завершена', unknown: 'Срок поддержки не указан',
}

export function SystemVersionPanel({ value }: { value: ReleaseStatus }) {
  const tone = value.support_status === 'expired' ? 'critical' : value.support_status === 'ending' ? 'warning' : value.support_status === 'supported' ? 'success' : 'neutral'
  return <Panel title="Версия системы">
    <div className="ops-health-summary">
      <strong>{value.version ?? 'Версия не определена'}</strong>
      <StatusBadge tone={tone}>{supportText[value.support_status]}</StatusBadge>
      {value.channel && <span>Канал: {value.channel}</span>}
      {value.support_class === 'lts' && <span>LTS</span>}
    </div>
    {value.supported_until && <p className="muted">Поддержка до {new Date(value.supported_until).toLocaleDateString('ru-RU')}</p>}
    {value.build_id && <p className="muted">Сборка {value.build_id}</p>}
  </Panel>
}
