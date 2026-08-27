import { useIsRevalidating } from '../lib/resource'

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
      className="global-progress"
      data-active={active ? 'true' : 'false'}
      role="progressbar"
    />
  )
}
