import { useIsRevalidating } from '../lib/resource'
import './GlobalProgress.css'

/**
 * Thin top-of-page bar that lights up while any cached resource is
 * revalidating in the background. Non-blocking — the stale content stays
 * fully interactive underneath.
 */
export function GlobalProgress() {
  const active = useIsRevalidating()
  return (
    <div
      aria-hidden={!active}
      aria-label="Обновление данных"
      className="global-progress"
      data-active={active ? 'true' : 'false'}
      role="progressbar"
    />
  )
}
