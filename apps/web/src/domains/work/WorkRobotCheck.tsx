import { useEffect, useRef, useState } from 'react'
import { api, type User } from '../../api'
import { canAccessRoute } from '../../app/routing/accessPolicy'
import { Button } from '../../design-system/actions/Button'
import { EmptyState, LoadingState } from '../../design-system/feedback/AsyncState'
import { CheckError, RobotCheckWorkspace } from '../robots/RobotCheckWorkspace'
import { classifyCheckError, parseRobotCheckTab } from '../robots/robotCheckUrl'
import type { DomainError } from '../../shared/api/classifyApiError'

type CheckClient = Pick<typeof api, 'emergencyResolve' | 'emergencySnapshot' | 'emergencySection'>

export function WorkRobotCheck({ robot, user, activeTab, onTabChange, onOpenTasks, onAuthorizationFailure, apiClient = api }: {
  robot: string; user: User; activeTab?: string; onTabChange(tab: string): void
  onOpenTasks(): void; onAuthorizationFailure?: (failure: DomainError) => void; apiClient?: CheckClient
}) {
  const [resolved, setResolved] = useState<Awaited<ReturnType<CheckClient['emergencyResolve']>> | null>(null)
  const [failure, setFailure] = useState<DomainError | null>(null)
  const [attempt, setAttempt] = useState(0)
  const owner = useRef(0)
  const notify = useRef(onAuthorizationFailure)
  useEffect(() => { notify.current = onAuthorizationFailure }, [onAuthorizationFailure])
  const allowed = canAccessRoute(user, 'robot-check')
  useEffect(() => {
    const generation = ++owner.current
    if (!allowed) return
    setResolved(null)
    setFailure(null)
    void apiClient.emergencyResolve(robot).then(value => {
      if (generation === owner.current) setResolved(value)
    }, error => {
      if (generation !== owner.current) return
      const classified = classifyCheckError(error)
      setFailure(classified)
      if (classified.kind === 'unauthorized' || classified.kind === 'forbidden') notify.current?.(classified)
    })
    return () => { owner.current += 1 }
  }, [robot, apiClient, attempt, allowed])

  if (!allowed) return <EmptyState title="Проверка робота недоступна для вашей роли" />
  if (failure) return <CheckError failure={failure} user={user} onRetry={() => setAttempt(value => value + 1)} />
  if (!resolved) return <LoadingState label="Находим робота" />
  const tab = parseRobotCheckTab(new URLSearchParams({ tab: activeTab ?? 'scheme' }), resolved.sections)
  return <RobotCheckWorkspace vin={resolved.vin} sections={resolved.sections} user={user} apiClient={apiClient}
    activeTab={tab} onTabChange={onTabChange} onAuthorizationFailure={onAuthorizationFailure}
    renderTasks={() => <Button onClick={onOpenTasks} variant="secondary">Открытые задачи робота</Button>} />
}
