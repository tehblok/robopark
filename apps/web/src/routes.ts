import type { AccessUser } from './app/routing/accessPolicy'
import { landingPathForUser } from './app/routing/accessPolicy'

export const NO_CABINET_PATH = '/no-cabinet'
export const pathForUser = landingPathForUser

export function hasCabinet(user: AccessUser): boolean {
  return landingPathForUser(user) !== NO_CABINET_PATH
}
