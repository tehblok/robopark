import type { AppRouteId } from './routeManifest'
import type { NestedStateKind } from './routeCoverageManifest'

export type RouteStateEvidence = {
  caseId: string
  routeId: AppRouteId
  stateId: string
  kind: NestedStateKind
  auth: 'authenticated' | 'unauthenticated'
  fixture: 'loaded' | 'loading' | 'empty' | 'error' | 'stale' | 'denied' | 'not-applicable'
  selector: string
  trigger?: { role: 'button'; name: string }
  notApplicableReason?: string
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
  item('operator-parks', 'loading', 'loading', 'loading', '.skeleton-list'),
  item('operator-parks', 'empty', 'empty', 'empty', 'text=Нет назначенных парков'),
  item('operator-parks', 'error', 'error', 'error', '[role="alert"]'),
  { ...item('operator-parks', 'stale', 'stale', 'not-applicable', ''), notApplicableReason: 'Экран парков не удерживает stale protected payload: при refresh failure показывается error.' },
  item('operator-parks', 'denied', 'denied', 'denied', 'main'),
  item('operator-parks', 'request-park', 'dialog', 'loaded', '[role="dialog"][aria-label="Запросить парк"]', { role: 'button', name: 'Запросить парк' }),
  item('operator-parks', 'request', 'form', 'loaded', '[role="dialog"] form', { role: 'button', name: 'Запросить парк' }),
] as const
