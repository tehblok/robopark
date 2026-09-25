import type { AppRouteId, UserRole } from './routeManifest'

export type CoverageAudience = UserRole | 'restricted' | 'guest'
export type NestedStateKind = 'view' | 'tab' | 'dialog' | 'form' | 'file' | 'loading' | 'empty' | 'error' | 'stale' | 'denied'

export type RouteCoverageState = { id: string; kind: NestedStateKind; testId: string }
export type RouteCoverageAction = {
  id: string
  roles: readonly CoverageAudience[]
  apiPermissionAssertions: readonly string[]
  permissionEvidence: readonly {
    role: CoverageAudience
    outcome: 'allow' | 'deny'
    apiPermissionAssertion: string
  }[]
}
export type RouteCoverageItem = {
  routeId: AppRouteId
  classicComponent: string
  roles: readonly CoverageAudience[]
  states: readonly RouteCoverageState[]
  actions: readonly RouteCoverageAction[]
}

const ALL_ROLES = ['royal', 'admin', 'operator', 'mechanic', 'driver', 'restricted'] as const
const INVENTORY_ROLES = ['royal', 'admin', 'operator', 'mechanic', 'restricted'] as const
const ANALYTICS_ROLES = ['royal', 'admin', 'operator', 'restricted'] as const
const MANAGER_ROLES = ['royal', 'admin', 'restricted'] as const
const BUILTIN_MANAGER_ROLES = ['royal', 'admin'] as const
const OWNERS = ['royal'] as const

function nested(routeId: AppRouteId, ...items: ReadonlyArray<readonly [string, NestedStateKind]>): RouteCoverageState[] {
  return items.map(([id, kind]) => ({ id, kind, testId: `route-coverage:${routeId}:${id}` }))
}

function asyncStates(routeId: AppRouteId, ...items: ReadonlyArray<readonly [string, NestedStateKind]>): RouteCoverageState[] {
  return nested(routeId,
    ['loading', 'loading'], ['empty', 'empty'], ['error', 'error'], ['stale', 'stale'], ['denied', 'denied'],
    ...items,
  )
}

type PendingAction = Omit<RouteCoverageAction, 'permissionEvidence'>

function action(id: string, roles: readonly CoverageAudience[], ...apiPermissionAssertions: string[]): PendingAction {
  return { id, roles, apiPermissionAssertions }
}

function route(
  routeId: AppRouteId,
  classicComponent: string,
  roles: readonly CoverageAudience[],
  states: readonly RouteCoverageState[] = [],
  actions: readonly PendingAction[] = [],
): RouteCoverageItem {
  const matrixRoles = roles.includes('guest') ? roles : ALL_ROLES
  return {
    routeId, classicComponent, roles, states,
    actions: actions.map(item => {
      const permissionEvidence = matrixRoles.map(role => {
        const outcome = item.roles.includes(role) ? 'allow' as const : 'deny' as const
        const matrixTest = ['stock-settings', 'receive-and-inventory', 'labels-and-export', 'catalog-delete-or-merge'].includes(item.id)
          ? 'apps/api/tests/test_inventory_catalog.py::test_inventory_action_role_matrix'
          : ['login', 'register'].includes(item.id)
            ? 'apps/api/tests/test_route_action_permissions.py::test_public_auth_action_http_matrix'
            : 'apps/api/tests/test_route_action_permissions.py::test_route_action_http_permission_matrix'
        return {
        role,
          outcome,
          apiPermissionAssertion: `${matrixTest}[${item.id}-${role}-${outcome}]`,
        }
      })
      return { ...item, apiPermissionAssertions: permissionEvidence.map(item => item.apiPermissionAssertion), permissionEvidence }
    }),
  }
}

const publicRoles = ['guest', ...ALL_ROLES] as const
const reportPermission = 'apps/api/tests/test_report_permissions.py::test_every_approved_standard_role_can_create_in_authorized_park'
const reportDeletePermission = 'apps/api/tests/test_reports.py::test_manager_cannot_delete_campaign_result_until_submission_is_removed'
const campaignManagerPermission = 'apps/api/tests/test_campaigns.py::test_non_admin_cannot_create_or_complete_foreign_campaign'
const inventoryStockPermission = 'apps/api/tests/test_inventory_catalog.py::test_catalog_update_stale_uniqueness_precheck_returns_409_with_existing_id'
const inventoryDocumentPermission = 'apps/api/tests/test_inventory_receipts.py::test_receipt_access_and_document_post_capability_are_independent'
const inventoryExportPermission = 'apps/api/tests/test_inventory_exports.py::test_export_role_scope_is_checked_before_inventory_queries'

/**
 * Acceptance inventory for every canonical route. Test IDs are stable keys
 * consumed by unit/E2E audits; they identify behavior, not implementation CSS.
 */
export const ROUTE_COVERAGE_MANIFEST: readonly RouteCoverageItem[] = [
  route('home', 'Home', publicRoles),
  route('login', 'Login', publicRoles, nested('login', ['credentials', 'form'], ['unauthorized', 'error']), [
    action('login', publicRoles, 'apps/api/tests/test_auth.py::test_login_me_logout_flow'),
  ]),
  route('register', 'Register', publicRoles, nested('register', ['account', 'form'], ['pending', 'stale']), [
    action('register', publicRoles, 'apps/api/tests/test_register.py::test_register_creates_pending_operator'),
  ]),
  route('change-password', 'ChangePassword', ALL_ROLES, nested('change-password', ['password', 'form'], ['error', 'error'])),
  route('no-cabinet', 'NoCabinet', ALL_ROLES, nested('no-cabinet', ['denied', 'denied'])),
  route('access-pending', 'OperatorPending', ALL_ROLES, nested('access-pending', ['pending', 'stale'])),
  route('access-rejected', 'OperatorRejected', ALL_ROLES, nested('access-rejected', ['denied', 'denied'])),
  route('mechanic-no-park', 'MechanicNoPark', ['mechanic'], nested('mechanic-no-park', ['empty', 'empty'], ['denied', 'denied'])),

  route('overview', 'OverviewPage.Classic', ALL_ROLES,
    asyncStates('overview', ['attention-queue', 'view'], ['quick-actions', 'view'])),
  route('operator-parks', 'OperatorParks.Classic', ['operator'],
    asyncStates('operator-parks', ['current-parks', 'view'], ['request-park', 'dialog'], ['request', 'form']), [
      action('request-park', ['operator'], 'apps/api/tests/test_park_requests.py::test_operator_creates_and_lists_own_request'),
    ]),
  route('work', 'IssueWorkbench.ClassicQueue', ALL_ROLES,
    asyncStates('work', ['queue', 'tab'], ['mine', 'tab'], ['filters', 'form'])),
  route('work-issue', 'IssueWorkbench.ClassicTask', ALL_ROLES,
    asyncStates('work-issue', ['repair', 'tab'], ['check', 'tab'], ['chat', 'tab'], ['open-related', 'tab'], ['closed-related', 'tab'], ['comment', 'form'], ['photo', 'file'], ['review', 'dialog']), [
      action('claim-and-transition', ['mechanic'], 'apps/api/tests/test_task_lifecycle.py::test_claim_requires_tracker_write_permission'),
      action('attach-photo', ['royal', 'admin', 'operator', 'mechanic', 'restricted'], 'apps/api/tests/test_task_lifecycle.py::test_submit_review_requires_attachment_permission_before_queuing_any_actions'),
    ]),
  route('robots', 'RobotsPage.Classic', ALL_ROLES,
    asyncStates('robots', ['search', 'form'], ['scanner', 'dialog'], ['camera', 'file'])),
  route('robot-detail', 'RobotPage.Classic', ALL_ROLES,
    asyncStates('robot-detail', ['summary', 'tab'], ['tasks', 'tab'], ['map', 'tab'])),
  route('robot-check', 'RobotCheckWorkspace.Classic', ALL_ROLES,
    asyncStates('robot-check', ['state', 'tab'], ['errors', 'tab'], ['readings', 'tab'], ['camera', 'file'], ['ignore-error', 'dialog']), [
      action('map-or-ignore-error', BUILTIN_MANAGER_ROLES, 'apps/api/tests/test_admin_diagnostic_rules.py::test_built_in_admin_roles_can_manage_rules'),
    ]),
  route('legacy-robot-check', 'LegacyEmergencyRedirect.Classic', ALL_ROLES,
    asyncStates('legacy-robot-check', ['resolve-robot', 'form'], ['scanner', 'dialog'], ['camera', 'file'])),

  route('inventory', 'InventoryPage.Classic', INVENTORY_ROLES,
    asyncStates('inventory', ['parts', 'tab'], ['receipts', 'tab'], ['counts', 'tab'], ['manage', 'tab'], ['export', 'tab'], ['stock', 'form'], ['receipt', 'form'], ['count', 'form'], ['labels', 'file'], ['post-confirmation', 'dialog']), [
      action('stock-settings', ['royal', 'admin', 'mechanic', 'restricted'], inventoryStockPermission),
      action('receive-and-inventory', ['royal', 'admin', 'mechanic', 'restricted'], inventoryDocumentPermission),
      action('labels-and-export', ['royal', 'admin', 'mechanic', 'restricted'], inventoryExportPermission),
      action('catalog-delete-or-merge', MANAGER_ROLES, 'apps/api/tests/test_inventory_catalog.py::test_merge_transfers_stock_and_movement_history_and_archives_source'),
    ]),
  route('reports', 'Reports.ClassicList', ALL_ROLES,
    asyncStates('reports', ['mine', 'tab'], ['inbox', 'tab'], ['filters', 'form'])),
  route('reports-new', 'Reports.ClassicCreate', ALL_ROLES,
    asyncStates('reports-new', ['kind', 'tab'], ['report', 'form'], ['attachment', 'file']), [
      action('create-report', ALL_ROLES, reportPermission),
    ]),
  route('report-detail', 'Reports.ClassicDetail', ALL_ROLES,
    asyncStates('report-detail', ['return', 'form'], ['resolve', 'dialog'], ['attachment', 'file'], ['hard-delete', 'dialog']), [
      action('return-or-resolve', ['royal', 'admin', 'operator'], 'apps/api/tests/test_reports.py::test_return_report_mechanic_forbidden'),
      action('hard-delete', BUILTIN_MANAGER_ROLES, reportDeletePermission),
    ]),
  route('campaigns', 'CampaignsPage.ClassicList', ALL_ROLES,
    asyncStates('campaigns', ['campaign-list', 'tab'], ['create', 'form'], ['parks', 'dialog']), [
      action('create-campaign', BUILTIN_MANAGER_ROLES, campaignManagerPermission),
    ]),
  route('campaign-detail', 'CampaignsPage.ClassicDetail', ALL_ROLES,
    asyncStates('campaign-detail', ['open', 'tab'], ['closed', 'tab'], ['settings', 'form'], ['ticket-result', 'form'], ['photo', 'file'], ['delete', 'dialog']), [
      action('update-or-delete-campaign', BUILTIN_MANAGER_ROLES, campaignManagerPermission, 'apps/api/tests/test_campaigns.py::test_manager_deletes_empty_campaign_but_archives_one_with_result'),
      action('submit-ticket-result', ALL_ROLES, 'apps/api/tests/test_campaigns.py::test_completion_requires_comment_and_photo_then_creates_operator_review'),
    ]),
  route('schedule', 'ScheduleWorkspace.Classic', ALL_ROLES,
    asyncStates('schedule', ['week', 'view'], ['month', 'view'], ['period', 'form'], ['notifications', 'view'])),
  route('analytics', 'AnalyticsWorkspace.Classic', ANALYTICS_ROLES,
    asyncStates('analytics', ['summary', 'tab'], ['flow', 'tab'], ['sla', 'tab'], ['park-comparison', 'form'])),
  route('system', 'SystemPage.Classic', BUILTIN_MANAGER_ROLES,
    asyncStates('system', ['metrics', 'view'], ['operations', 'view'], ['confirmation', 'dialog'])),

  route('admin', 'ManagementPage.Classic', MANAGER_ROLES,
    asyncStates('admin', ['users', 'tab'], ['roles', 'tab'], ['parks', 'tab'], ['requests', 'tab'])),
  route('admin-settings', 'Admin.ClassicSettings', MANAGER_ROLES,
    asyncStates('admin-settings', ['integrations', 'tab'], ['tracker-policy', 'form'], ['registration', 'form'], ['backup', 'file'], ['restore', 'dialog']), [
      action('change-platform-settings', MANAGER_ROLES, 'apps/api/tests/test_tracker_policy_admin_settings.py::test_admin_can_update_tracker_policy'),
      action('backup-restore-update', OWNERS, 'apps/api/tests/test_ops_http.py::test_admin_cannot_create_snapshot'),
    ]),
  route('admin-users', 'UserManagementPage.Classic', MANAGER_ROLES,
    asyncStates('admin-users', ['accounts', 'tab'], ['activity', 'tab'], ['user', 'dialog'], ['permissions', 'form'], ['last-location', 'stale']), [
      action('manage-user', MANAGER_ROLES, 'apps/api/tests/test_management_permissions.py::test_user_manager_cannot_escalate_role_or_partially_mutate_target'),
      action('approve-user', OWNERS, 'apps/api/tests/test_admin_user_access.py::test_admin_cannot_change_access_status'),
    ]),
  route('admin-roles', 'RoleManagementPage.Classic', MANAGER_ROLES,
    asyncStates('admin-roles', ['roles', 'tab'], ['role', 'dialog'], ['permissions', 'form']), [
      action('manage-role', MANAGER_ROLES, 'apps/api/tests/test_management_permissions.py::test_owner_can_create_and_assign_privileged_roles'),
    ]),
  route('admin-tracker', 'LegacyRedirect.Classic', MANAGER_ROLES,
    asyncStates('admin-tracker', ['redirect', 'loading'])),
  route('admin-robot-check', 'AdminEmergencyConfig.Classic', MANAGER_ROLES,
    asyncStates('admin-robot-check', ['sections', 'tab'], ['readings', 'tab'], ['rules', 'tab'], ['unknowns', 'tab'], ['rule', 'form'], ['preview', 'dialog'], ['ignore', 'dialog']), [
      action('map-diagnostic', BUILTIN_MANAGER_ROLES, 'apps/api/tests/test_admin_diagnostic_rules.py::test_built_in_admin_roles_can_manage_rules'),
      action('ignore-diagnostic', BUILTIN_MANAGER_ROLES, 'apps/api/tests/test_diagnostic_unknowns.py::test_ignore_hides_the_raw_signal_until_reopen'),
    ]),
  route('not-found', 'RouteFallback', publicRoles, nested('not-found', ['not-found', 'empty'])),
] as const
