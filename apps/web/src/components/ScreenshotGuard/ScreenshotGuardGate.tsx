import { useEffect } from 'react'
import { useAuth } from '../../auth-context'
import { PersistentWatermark } from './PersistentWatermark'
import { ScreenshotGuard } from './ScreenshotGuard'

export function ScreenshotGuardGate() {
  const { user } = useAuth()

  useEffect(() => {
    if (!user?.screenshot_guard) {
      document.body.classList.remove('screenshot-protected')
      return
    }
    document.body.classList.add('screenshot-protected')
    return () => {
      document.body.classList.remove('screenshot-protected')
    }
  }, [user?.screenshot_guard])

  if (!user?.screenshot_guard) return null

  return (
    <>
      <PersistentWatermark username={user.username} userId={user.id} />
      <ScreenshotGuard username={user.username} userId={user.id} />
    </>
  )
}
