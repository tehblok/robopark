import { Link } from 'react-router-dom'
import { useAuth } from '../auth-context'
import { PageShell } from '../components/PageShell'
import { TrackerWorkspace } from '../components/tracker/TrackerWorkspace'
import { EmptyBlock } from '../components/ui/Feedback'
import { useParkContext } from '../park-context'
import { MechanicTasks } from './MechanicTasks'
import { OperatorBlockers } from './OperatorBlockers'

function StaffTasks() {
  const { user } = useAuth()
  const { parkId, parks } = useParkContext()
  const selected = parks.find((park) => park.id === parkId)
  const defaultQueue = (selected?.tracker_queue || 'SDCFLEETOPS').trim() || 'SDCFLEETOPS'
  const defaultPark = selected?.tag?.trim() || undefined
  const canWrite = (user?.permissions ?? []).includes('tracker.write')

  return (
    <PageShell
      subtitle={
        selected
          ? `Очередь ${defaultQueue}${defaultPark ? ` · тег ${defaultPark}` : ''}`
          : 'Выберите парк в верхней панели — подставим очередь и тег.'
      }
      title="Задачи"
    >
      <TrackerWorkspace
        allowUntagged
        canWrite={canWrite}
        defaultPark={defaultPark}
        defaultQueue={defaultQueue}
        key={`${parkId ?? 'none'}:${defaultQueue}:${defaultPark ?? ''}`}
      />
    </PageShell>
  )
}

export function Tasks() {
  const { user } = useAuth()
  if (!user) return null

  const perms = user.permissions ?? []

  if (user.role === 'mechanic') {
    return <MechanicTasks />
  }
  if (user.role === 'operator') {
    return <OperatorBlockers />
  }

  const canTracker =
    perms.includes('tracker.read') ||
    perms.includes('tracker.write') ||
    perms.includes('nav.admin.tracker')
  if (canTracker) {
    return <StaffTasks />
  }

  const adminLink = perms.includes('nav.admin')

  return (
    <PageShell
      subtitle="Список задач по парку доступен механикам и операторам."
      title="Задачи"
    >
      <EmptyBlock
        action={
          adminLink ? (
            <Link className="btn btn-secondary" to="/admin">
              Администрирование
            </Link>
          ) : undefined
        }
        hint="Для этой роли нет очереди задач парка и нет доступа к Tracker."
        icon="📋"
        title="Нет списка задач для этой роли"
      />
    </PageShell>
  )
}
