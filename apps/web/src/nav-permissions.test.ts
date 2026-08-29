import { describe, expect, it } from 'vitest'
import { navItemsForPermissions } from './nav-permissions'

it('hides stub nav items even when the permission is granted', () => {
  const items = navItemsForPermissions(['nav.map', 'nav.dashboard', 'nav.help'])
  expect(items.map((item) => item.id)).toEqual(['dashboard'])
})
