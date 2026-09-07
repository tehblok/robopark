import { Button } from '../../design-system/actions/Button'
import { OpsAlert } from './OpsAlert'
import { useEffect } from 'react'
import { api } from '../../api'
import { LoadingState } from '../../design-system/feedback/AsyncState'
import { StatusBadge } from '../../design-system/status/StatusBadge'
import { Panel } from '../PageShell'
import { age, opsText, staleHealth, useOpsResource } from './opsPresentation'

export function SystemHealthPanel({ onRepair, busy, revision = 0 }: { onRepair: () => void; busy: boolean; revision?: number }) {
  const { data, error, refresh } = useOpsResource('ops:health', api.opsSystemHealth)
  useEffect(() => { if (revision > 0) void refresh() }, [revision, refresh])
  const stale = staleHealth(data?.generated_at)
  return <Panel title="Здоровье системы" hint="Состояние хоста и последней резервной копии">
    {!data && !error && <LoadingState label="Проверяем состояние системы" />}
    {error ? <OpsAlert>Нет связи с хостом. {data ? 'Показаны последние полученные данные.' : 'Состояние временно недоступно.'}</OpsAlert> : null}
    {data && <>
      <div className="ops-health-summary">
        <StatusBadge tone={data.overall === 'ok' ? 'success' : data.overall === 'degraded' ? 'warning' : 'neutral'}>{data.overall === 'ok' ? 'Система работает' : data.overall === 'degraded' ? 'Есть проблемы' : 'Состояние неизвестно'}</StatusBadge>
        <span>Версия {opsText(data.version)} · Git {opsText(data.git_sha?.slice(0, 7))}</span>
        <span>Проверено: {age(data.generated_at)}</span>
      </div>
      {stale && <OpsAlert>Данные устарели. Новое состояние появится после проверки хоста.</OpsAlert>}
      <ul className="ops-checks">
        {data.checks.map(check => <li key={check.code}><span>{opsText(check.message, 'Проверка хоста')}</span><StatusBadge tone={check.status === 'ok' ? 'success' : check.status === 'failed' ? 'critical' : 'warning'}>{check.status === 'ok' ? 'В порядке' : check.status === 'failed' ? 'Ошибка' : 'Требует внимания'}</StatusBadge></li>)}
      </ul>
      <p>Резервная копия: {data.last_backup.status === 'unknown' ? 'нет данных' : data.last_backup.status === 'failed' ? 'последняя попытка не удалась' : `создана ${age(data.last_backup.completed_at)}`}</p>
      {data.update.state === 'rolled_back' && <OpsAlert>Восстановлена предыдущая версия: обновление не прошло проверку работоспособности.</OpsAlert>}
      {data.update.state === 'updating' && <OpsAlert tone="info">Хост устанавливает обновление.</OpsAlert>}
      {data.update.state === 'maintenance' && <OpsAlert>Хост выполняет технические работы.</OpsAlert>}
      {data.update.publication === 'degraded' && <OpsAlert>Публикация через Tuna недоступна.</OpsAlert>}
      <div className="form-actions"><Button variant="secondary" type="button" disabled={busy || !data.checks.some(check => check.repair && check.status !== 'ok')} onClick={onRepair}>Исправить безопасные проблемы</Button></div>
      <p className="muted">Исправление перезапускает только разрешённые службы. Результат каждой попытки появится в текущей операции.</p>
    </>}
  </Panel>
}
