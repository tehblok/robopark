import type { AppRouteId } from './routeManifest'
import type { NestedStateKind } from './routeCoverageManifest'

export type RouteStateEvidence = {
  caseId: string
  routeId: AppRouteId
  stateId: string
  kind: NestedStateKind
  auth: 'authenticated' | 'unauthenticated'
  fixture: 'loaded' | 'loading' | 'empty' | 'error' | 'stale' | 'denied'
  selector: string
  trigger?: { role: 'button'; name: string }
}

const item = (routeId: AppRouteId, stateId: string, kind: NestedStateKind, fixture: RouteStateEvidence['fixture'], selector: string, trigger?: RouteStateEvidence['trigger']): RouteStateEvidence => ({
  caseId: `route-coverage:${routeId}:${stateId}`, routeId, stateId, kind, auth: 'authenticated', fixture, selector, trigger,
})

export const ROUTE_STATE_EVIDENCE: readonly RouteStateEvidence[] = [
  item('overview', 'loading', 'loading', 'loading', '[aria-label="Загружаем сводку"]'),
  item('overview', 'empty', 'empty', 'empty', 'text=Нет задач'),
  item('overview', 'error', 'error', 'error', '[role="alert"]'),
  item('overview', 'stale', 'stale', 'stale', 'text=Показаны последние'),
  item('overview', 'denied', 'denied', 'denied', 'text=Нет доступа'),
  item('overview', 'attention-queue', 'view', 'loaded', 'h2:has-text("Очередь внимания")'),
  item('overview', 'quick-actions', 'view', 'loaded', 'a,button'),
  item('operator-parks', 'current-parks', 'view', 'loaded', '.park-card-title'),
  item('operator-parks', 'request-park', 'dialog', 'loaded', '[role="dialog"][aria-label="Запросить парк"]', { role: 'button', name: 'Запросить парк' }),
  item('operator-parks', 'request', 'form', 'loaded', '[role="dialog"] form', { role: 'button', name: 'Запросить парк' }),
] as const
