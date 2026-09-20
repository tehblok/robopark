import type { IconName } from '../../design-system/icons/Icon'
import { ru } from '../../i18n/ru'

export const SYSTEM_USER_ROLES = ['royal', 'admin', 'operator', 'mechanic', 'driver'] as const
export type UserRole = typeof SYSTEM_USER_ROLES[number]

export function isSystemUserRole(role: string): role is UserRole {
  return (SYSTEM_USER_ROLES as readonly string[]).includes(role)
}

export type AppRouteId =
  | 'home' | 'login' | 'register' | 'change-password' | 'no-cabinet'
  | 'access-pending' | 'access-rejected' | 'mechanic-no-park'
  | 'overview' | 'operator-parks' | 'work' | 'robots' | 'robot-check'
  | 'work-issue' | 'robot-detail' | 'legacy-robot-check'
  | 'campaigns' | 'campaign-detail'
  | 'inventory'
  | 'analytics' | 'reports' | 'reports-new' | 'report-detail' | 'admin' | 'admin-settings' | 'admin-users' | 'admin-roles' | 'admin-tracker'
  | 'admin-robot-check' | 'not-found'
export type NavGroup = 'operations' | 'collaboration' | 'insights' | 'administration'
export type NavSurface = 'desktop' | 'mobile'
export type AccessPrerequisite = 'password-changed' | 'approved' | 'mechanic-has-park'
export type RouteNav = {
  group: NavGroup
  desktopOrder: number
  mobilePriority?: Partial<Record<UserRole, number>>
}
export type RouteManifestItem = {
  id: AppRouteId
  path: string
  legacyPaths?: readonly string[]
  label: string
  icon: IconName
  permission?: string
  anyPermissions?: readonly string[]
  prerequisites?: readonly AccessPrerequisite[]
  surface: 'public' | 'standalone' | 'shell'
  redirectTo?: string
  nav?: RouteNav
}
export type NavigationItem = Omit<Pick<RouteManifestItem, 'id' | 'path' | 'label' | 'icon'>, 'id'> & {
  id: AppRouteId | 'work-mine'
  group: NavGroup
  priority: number
}

const SHELL_PREREQUISITES = [
  'password-changed',
  'approved',
  'mechanic-has-park',
] as const satisfies readonly AccessPrerequisite[]

export const ROUTE_MANIFEST: readonly RouteManifestItem[] = [
  { id: 'home', path: '/', label: ru.brand, icon: 'overview', surface: 'public' },
  { id: 'login', path: '/login', label: 'Вход', icon: 'forward', surface: 'public' },
  { id: 'register', path: '/register', label: 'Регистрация', icon: 'users', surface: 'public' },
  {
    id: 'change-password',
    path: '/change-password',
    label: 'Смена пароля',
    icon: 'settings',
    surface: 'standalone',
  },
  {
    id: 'no-cabinet',
    path: '/no-cabinet',
    label: 'Нет доступных разделов',
    icon: 'warning',
    surface: 'standalone',
  },
  {
    id: 'access-pending',
    path: '/access/pending',
    legacyPaths: ['/operator/pending'],
    label: 'Доступ на рассмотрении',
    icon: 'clock',
    surface: 'standalone',
  },
  {
    id: 'access-rejected',
    path: '/access/rejected',
    legacyPaths: ['/operator/rejected'],
    label: 'Доступ отклонён',
    icon: 'critical',
    surface: 'standalone',
  },
  {
    id: 'mechanic-no-park',
    path: '/mechanic/no-park',
    label: 'Парк не назначен',
    icon: 'parks',
    surface: 'standalone',
  },
  {
    id: 'overview',
    path: '/overview',
    label: ru.nav.dashboard,
    icon: 'overview',
    permission: 'nav.dashboard',
    prerequisites: SHELL_PREREQUISITES,
    surface: 'shell',
    nav: {
      group: 'operations',
      desktopOrder: 10,
      mobilePriority: { royal: 1, admin: 2, operator: 1, mechanic: 4, driver: 3 },
    },
  },
  {
    id: 'operator-parks',
    path: '/operator/parks',
    label: 'Парки',
    icon: 'parks',
    prerequisites: ['password-changed', 'approved'],
    surface: 'shell',
    nav: { group: 'operations', desktopOrder: 20 },
  },
  {
    id: 'work',
    path: '/work',
    legacyPaths: ['/tasks', '/dashboard', '/operator'],
    label: ru.nav.tasks,
    icon: 'work',
    anyPermissions: ['nav.tasks', 'nav.dashboard', 'nav.admin.tracker'],
    prerequisites: SHELL_PREREQUISITES,
    surface: 'shell',
    nav: {
      group: 'operations',
      desktopOrder: 30,
      mobilePriority: { royal: 3, admin: 3, operator: 2, mechanic: 1 },
    },
  },
  {
    id: 'work-issue', path: '/work/:issueKey', label: 'Работа', icon: 'work',
    anyPermissions: ['nav.tasks', 'nav.dashboard', 'nav.admin.tracker'], prerequisites: SHELL_PREREQUISITES, surface: 'shell',
  },
  {
    id: 'robots',
    path: '/robots',
    legacyPaths: ['/robots/search'],
    label: ru.nav.robot_search,
    icon: 'robot',
    permission: 'nav.robot_search',
    prerequisites: SHELL_PREREQUISITES,
    surface: 'shell',
    nav: {
      group: 'operations',
      desktopOrder: 40,
      mobilePriority: { operator: 3, mechanic: 3, driver: 2 },
    },
  },
  {
    id: 'robot-detail', path: '/robots/:vin', label: 'Робот', icon: 'robot',
    anyPermissions: ['nav.robot_search', 'nav.emergency'], prerequisites: SHELL_PREREQUISITES, surface: 'shell',
  },
  {
    id: 'robot-check', path: '/robots/:vin/check', label: ru.nav.emergency, icon: 'robot-check',
    permission: 'nav.emergency', prerequisites: SHELL_PREREQUISITES, surface: 'shell',
  },
  {
    id: 'legacy-robot-check',
    path: '/emergency',
    label: ru.nav.emergency,
    icon: 'robot-check',
    permission: 'nav.emergency',
    prerequisites: SHELL_PREREQUISITES,
    surface: 'shell',
  },
  {
    id: 'inventory',
    path: '/inventory',
    label: 'Склад',
    icon: 'work',
    permission: 'nav.inventory',
    prerequisites: SHELL_PREREQUISITES,
    surface: 'shell',
    nav: { group: 'operations', desktopOrder: 50 },
  },
  {
    id: 'reports',
    path: '/reports',
    label: ru.nav.reports,
    icon: 'reports',
    permission: 'nav.reports',
    prerequisites: SHELL_PREREQUISITES,
    surface: 'shell',
    nav: {
      group: 'collaboration',
      desktopOrder: 60,
      mobilePriority: { driver: 4 },
    },
  },
  {
    id: 'campaigns',
    path: '/campaigns',
    label: 'СК и оклейка',
    icon: 'work',
    prerequisites: SHELL_PREREQUISITES,
    surface: 'shell',
    nav: { group: 'collaboration', desktopOrder: 65, mobilePriority: { operator: 4 } },
  },
  {
    id: 'campaign-detail', path: '/campaigns/:campaignId', label: 'Кампания', icon: 'work',
    prerequisites: SHELL_PREREQUISITES, surface: 'shell',
  },
  {
    id: 'reports-new',
    path: '/reports/new',
    label: 'Создать репорт',
    icon: 'reports',
    permission: 'nav.reports',
    prerequisites: SHELL_PREREQUISITES,
    surface: 'shell',
  },
  {
    id: 'report-detail',
    path: '/reports/:reportId',
    label: 'Репорт',
    icon: 'reports',
    permission: 'nav.reports',
    prerequisites: SHELL_PREREQUISITES,
    surface: 'shell',
  },
  {
    id: 'analytics',
    path: '/analytics',
    label: ru.nav.analytics,
    icon: 'analytics',
    permission: 'nav.analytics',
    prerequisites: SHELL_PREREQUISITES,
    surface: 'shell',
    nav: { group: 'insights', desktopOrder: 70 },
  },
  {
    id: 'admin',
    path: '/admin',
    label: 'Управление',
    icon: 'settings',
    anyPermissions: ['nav.admin', 'users.manage', 'roles.manage', 'parks.manage'],
    prerequisites: SHELL_PREREQUISITES,
    surface: 'shell',
    nav: {
      group: 'administration',
      desktopOrder: 80,
      mobilePriority: { royal: 2, admin: 1 },
    },
  },
  {
    id: 'admin-settings',
    path: '/admin/settings',
    label: 'Настройки управления',
    icon: 'settings',
    anyPermissions: ['nav.admin', 'parks.manage'],
    prerequisites: SHELL_PREREQUISITES,
    surface: 'shell',
  },
  {
    id: 'admin-users',
    path: '/admin/users',
    label: 'Пользователи',
    icon: 'users',
    permission: 'users.manage',
    prerequisites: SHELL_PREREQUISITES,
    surface: 'shell',
  },
  {
    id: 'admin-roles',
    path: '/admin/roles',
    label: 'Роли',
    icon: 'settings',
    permission: 'roles.manage',
    prerequisites: SHELL_PREREQUISITES,
    surface: 'shell',
  },
  {
    id: 'admin-tracker',
    path: '/admin/tracker',
    redirectTo: '/work',
    label: 'Startrek',
    icon: 'integration',
    permission: 'nav.admin.tracker',
    prerequisites: SHELL_PREREQUISITES,
    surface: 'shell',
  },
  {
    id: 'admin-robot-check',
    path: '/admin/emergency/config',
    label: 'Настройка проверки робота',
    icon: 'safety',
    permission: 'nav.admin.emergency',
    prerequisites: SHELL_PREREQUISITES,
    surface: 'shell',
    nav: { group: 'administration', desktopOrder: 100 },
  },
  {
    id: 'not-found',
    path: '*',
    label: 'Страница не найдена',
    icon: 'warning',
    surface: 'standalone',
  },
] as const
