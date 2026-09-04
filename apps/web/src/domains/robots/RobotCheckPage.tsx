import { api } from '../../api'
import type { RobotCheckApiClient } from './RobotCheckWorkspace'
import type { RobotResolverApiClient } from './RobotResolver'
import { RobotPage } from './RobotPage'

// Compatibility entry only: both URLs use the same principal and snapshot owner.
export function RobotCheckPage({ resolverClient = api, checkClient = api }: {
  resolverClient?: RobotResolverApiClient; checkClient?: RobotCheckApiClient
}) {
  return <RobotPage resolverClient={resolverClient} checkClient={checkClient} />
}
