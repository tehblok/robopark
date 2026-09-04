import type { Park, User } from '../../src/api'

export const northPark: Park = { id: 7, name: 'Северный парк', tag: 'north', is_active: true }

export const operatorUser: User = {
  id: 20,
  username: 'operator-e2e',
  role: 'operator',
  access_status: 'approved',
  permissions: [
    'nav.dashboard', 'nav.tasks', 'nav.robot_search', 'nav.emergency',
    'nav.analytics', 'nav.reports', 'tracker.read', 'tracker.write',
    'tracker.attach', 'reports.create', 'reports.resolve',
  ],
  parks: [northPark],
}

export const mechanicUser: User = {
  id: 30,
  username: 'mechanic-e2e',
  role: 'mechanic',
  access_status: 'approved',
  permissions: [
    'nav.dashboard', 'nav.tasks', 'nav.robot_search', 'nav.emergency',
    'nav.reports', 'tracker.read', 'tracker.write', 'tracker.attach',
    'reports.create',
  ],
  parks: [northPark],
}
