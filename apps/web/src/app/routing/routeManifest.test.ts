import { describe, expect, it } from 'vitest'
import type { Park, User } from '../../api'
import { navigationForUser } from './accessPolicy'
import { ROUTE_MANIFEST } from './routeManifest'

const north: Park = {
  id: 7,
  name: 'North Park',
  tag: 'NORTH',
  is_active: true,
  tracker_queue: 'NORTHROBOTS',
  tracker_priority: 'normal',
  tracker_type: 'task',
  group_id: 17,
  chat_id: 27,
  feature_reports: true,
  feature_blockers: true,
  feature_sla_repair: true,
  feature_backlog_alerts: true,
}

function user(overrides: Partial<User>): User {
  return {
    id: 1,
    username: 'manifest-user',
    role: 'royal',
    access_status: 'approved',
    permissions: [],
    tracker_login: 'manifest-user',
    must_change_password: false,
    screenshot_guard: false,
    parks: [north],
    ...overrides,
  }
}

const allPermissions = [
  'nav.dashboard',
  'nav.tasks',
  'nav.robot_search',
  'nav.emergency',
  'nav.analytics',
  'nav.reports',
  'nav.inventory',
  'nav.admin',
  'nav.admin.tracker',
  'nav.admin.emergency',
]

describe('ROUTE_MANIFEST', () => {
  it('keeps every canonical route and compatibility alias in one explicit record', () => {
    expect(ROUTE_MANIFEST).toEqual([
      { id: 'home', path: '/', label: 'Робопарк', icon: 'overview', surface: 'public' },
      { id: 'login', path: '/login', label: 'Вход', icon: 'forward', surface: 'public' },
      { id: 'register', path: '/register', label: 'Регистрация', icon: 'users', surface: 'public' },
      { id: 'change-password', path: '/change-password', label: 'Смена пароля', icon: 'settings', surface: 'standalone' },
      { id: 'no-cabinet', path: '/no-cabinet', label: 'Нет доступных разделов', icon: 'warning', surface: 'standalone' },
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
        redirectTo: '/work',
        label: 'Обзор',
        icon: 'overview',
        permission: 'nav.dashboard',
        prerequisites: ['password-changed', 'approved', 'mechanic-has-park'],
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
        label: 'Работа',
        icon: 'work',
        anyPermissions: ['nav.tasks', 'nav.dashboard', 'nav.admin.tracker'],
        prerequisites: ['password-changed', 'approved', 'mechanic-has-park'],
        surface: 'shell',
        nav: {
          group: 'operations',
          desktopOrder: 30,
          mobilePriority: { royal: 3, admin: 3, operator: 2, mechanic: 1 },
        },
      },
      {
        id: 'work-issue', path: '/work/:issueKey', label: 'Работа', icon: 'work',
        anyPermissions: ['nav.tasks', 'nav.dashboard', 'nav.admin.tracker'], prerequisites: ['password-changed', 'approved', 'mechanic-has-park'], surface: 'shell',
      },
      {
        id: 'robots',
        path: '/robots',
        legacyPaths: ['/robots/search'],
        label: 'Роботы',
        icon: 'robot',
        permission: 'nav.robot_search',
        prerequisites: ['password-changed', 'approved', 'mechanic-has-park'],
        surface: 'shell',
        nav: {
          group: 'operations',
          desktopOrder: 40,
          mobilePriority: { operator: 3, mechanic: 3, driver: 2 },
        },
      },
      {
        id: 'robot-detail', path: '/robots/:vin', label: 'Робот', icon: 'robot',
        anyPermissions: ['nav.robot_search', 'nav.emergency'], prerequisites: ['password-changed', 'approved', 'mechanic-has-park'], surface: 'shell',
      },
      {
        id: 'robot-check', path: '/robots/:vin/check', label: 'Проверка робота', icon: 'robot-check',
        permission: 'nav.emergency', prerequisites: ['password-changed', 'approved', 'mechanic-has-park'], surface: 'shell',
      },
      {
        id: 'legacy-robot-check',
        path: '/emergency',
        label: 'Проверка робота',
        icon: 'robot-check',
        permission: 'nav.emergency',
        prerequisites: ['password-changed', 'approved', 'mechanic-has-park'],
        surface: 'shell',
      },
      {
        id: 'inventory',
        path: '/inventory',
        label: 'Склад',
        icon: 'work',
        permission: 'nav.inventory',
        prerequisites: ['password-changed', 'approved', 'mechanic-has-park'],
        surface: 'shell',
        nav: { group: 'operations', desktopOrder: 50 },
      },
      {
        id: 'reports',
        path: '/reports',
        label: 'Репорты',
        icon: 'reports',
        permission: 'nav.reports',
        prerequisites: ['password-changed', 'approved', 'mechanic-has-park'],
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
        prerequisites: ['password-changed', 'approved', 'mechanic-has-park'],
        surface: 'shell',
        nav: { group: 'collaboration', desktopOrder: 65, mobilePriority: { operator: 4 } },
      },
      {
        id: 'campaign-detail', path: '/campaigns/:campaignId', label: 'Кампания', icon: 'work',
        prerequisites: ['password-changed', 'approved', 'mechanic-has-park'], surface: 'shell',
      },
      {
        id: 'reports-new',
        path: '/reports/new',
        label: 'Создать репорт',
        icon: 'reports',
        permission: 'nav.reports',
        prerequisites: ['password-changed', 'approved', 'mechanic-has-park'],
        surface: 'shell',
      },
      {
        id: 'report-detail',
        path: '/reports/:reportId',
        label: 'Репорт',
        icon: 'reports',
        permission: 'nav.reports',
        prerequisites: ['password-changed', 'approved', 'mechanic-has-park'],
        surface: 'shell',
      },
      {
        id: 'analytics',
        path: '/analytics',
        label: 'Аналитика',
        icon: 'analytics',
        permission: 'nav.analytics',
        prerequisites: ['password-changed', 'approved', 'mechanic-has-park'],
        surface: 'shell',
        nav: { group: 'insights', desktopOrder: 70 },
      },
      {
        id: 'admin',
        path: '/admin',
        label: 'Администрирование',
        icon: 'settings',
        anyPermissions: ['nav.admin', 'users.manage', 'roles.manage', 'parks.manage'],
        prerequisites: ['password-changed', 'approved', 'mechanic-has-park'],
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
        prerequisites: ['password-changed', 'approved', 'mechanic-has-park'],
        surface: 'shell',
      },
      {
        id: 'admin-users',
        path: '/admin/users',
        label: 'Пользователи',
        icon: 'users',
        permission: 'users.manage',
        prerequisites: ['password-changed', 'approved', 'mechanic-has-park'],
        surface: 'shell',
      },
      {
        id: 'admin-roles',
        path: '/admin/roles',
        label: 'Роли',
        icon: 'settings',
        permission: 'roles.manage',
        prerequisites: ['password-changed', 'approved', 'mechanic-has-park'],
        surface: 'shell',
      },
      {
        id: 'admin-tracker',
        path: '/admin/tracker',
        redirectTo: '/work',
        label: 'Startrek',
        icon: 'integration',
        permission: 'nav.admin.tracker',
        prerequisites: ['password-changed', 'approved', 'mechanic-has-park'],
        surface: 'shell',
      },
      {
        id: 'admin-robot-check',
        path: '/admin/emergency/config',
        label: 'Настройка проверки робота',
        icon: 'safety',
        permission: 'nav.admin.emergency',
        prerequisites: ['password-changed', 'approved', 'mechanic-has-park'],
        surface: 'shell',
        nav: { group: 'administration', desktopOrder: 100 },
      },
      { id: 'not-found', path: '*', label: 'Страница не найдена', icon: 'warning', surface: 'standalone' },
    ])
  })

  it('routes retired dashboards through the single Work home', () => {
    const work = ROUTE_MANIFEST.find((route) => route.id === 'work')
    const tracker = ROUTE_MANIFEST.find((route) => route.id === 'admin-tracker')

    expect(work?.legacyPaths).toEqual(['/tasks', '/dashboard', '/operator'])
    expect(tracker?.nav).toBeUndefined()
  })

  it('does not duplicate any canonical or legacy path', () => {
    const paths = ROUTE_MANIFEST.flatMap((route) => [route.path, ...(route.legacyPaths ?? [])])

    expect(paths).toHaveLength(new Set(paths).size)
    expect(paths.filter((path) => path === '/dashboard')).toHaveLength(1)
    expect(paths.filter((path) => path === '/operator')).toHaveLength(1)
  })

  it('declares every public and standalone route exactly once without a navigation permission', () => {
    const publicAndStandaloneIds = [
      'home',
      'login',
      'register',
      'change-password',
      'no-cabinet',
      'access-pending',
      'access-rejected',
      'mechanic-no-park',
      'not-found',
    ]

    expect(ROUTE_MANIFEST.filter((route) => route.surface !== 'shell').map((route) => route.id))
      .toEqual(publicAndStandaloneIds)
    for (const id of publicAndStandaloneIds) {
      const matches = ROUTE_MANIFEST.filter((route) => route.id === id)
      expect(matches).toHaveLength(1)
      expect(matches[0]?.permission).toBeUndefined()
      expect(matches[0]?.nav).toBeUndefined()
    }
  })
})

describe('navigation ordering', () => {
  const expectedDesktop = {
    royal: ['overview', 'work', 'robots', 'inventory', 'reports', 'campaigns', 'analytics', 'admin', 'admin-robot-check'],
    admin: ['overview', 'work', 'robots', 'inventory', 'reports', 'campaigns', 'analytics', 'admin', 'admin-robot-check'],
    operator: ['overview', 'operator-parks', 'work', 'robots', 'inventory', 'reports', 'campaigns', 'analytics', 'admin', 'admin-robot-check'],
    mechanic: ['overview', 'work', 'robots', 'inventory', 'reports', 'campaigns', 'analytics', 'admin', 'admin-robot-check'],
    driver: ['overview', 'work', 'robots', 'inventory', 'reports', 'campaigns', 'analytics', 'admin', 'admin-robot-check'],
    field_lead: ['overview', 'work', 'robots', 'inventory', 'reports', 'campaigns', 'analytics', 'admin', 'admin-robot-check'],
  } as const

  const expectedMobile = {
    royal: ['overview', 'admin', 'work', 'robots', 'inventory', 'reports', 'campaigns', 'analytics', 'admin-robot-check'],
    admin: ['admin', 'overview', 'work', 'robots', 'inventory', 'reports', 'campaigns', 'analytics', 'admin-robot-check'],
    operator: ['overview', 'work', 'robots', 'campaigns', 'operator-parks', 'inventory', 'reports', 'analytics', 'admin', 'admin-robot-check'],
    mechanic: ['work', 'robots', 'overview', 'inventory', 'reports', 'campaigns', 'analytics', 'admin', 'admin-robot-check'],
    driver: ['robots', 'overview', 'reports', 'work', 'inventory', 'campaigns', 'analytics', 'admin', 'admin-robot-check'],
    field_lead: ['overview', 'work', 'robots', 'inventory', 'reports', 'campaigns', 'analytics', 'admin', 'admin-robot-check'],
  } as const

  it.each(Object.entries(expectedDesktop))('keeps desktop order stable for %s', (role, expected) => {
    const actual = navigationForUser(user({ role, permissions: allPermissions }), 'desktop')

    expect(actual.map((item) => item.id)).toEqual(expected)
  })

  it.each(Object.entries(expectedMobile))('keeps a hand-authored mobile order for %s', (role, expected) => {
    const actual = navigationForUser(user({ role, permissions: allPermissions }), 'mobile')

    expect(actual.map((item) => item.id)).toEqual(expected)
    expect(actual.filter((item) => item.priority <= 4)).toHaveLength(
      role === 'field_lead' ? 0 : role === 'operator' ? 4 : 3,
    )
  })
})
