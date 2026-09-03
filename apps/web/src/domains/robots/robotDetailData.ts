import { api, type Blocker, type User } from '../../api'

export type RobotDetailApiClient = Pick<typeof api,
  'emergencyResolve' | 'emergencySnapshot' | 'mechanicRobotTickets' | 'operatorRobotTickets' | 'trackerRobotTickets'>

export async function resolveRobotDetail(client: RobotDetailApiClient, reference: string) {
  const resolved = await client.emergencyResolve(reference)
  const snapshot = await client.emergencySnapshot(resolved.vin)
  return { ...resolved, snapshot }
}

export async function loadRelatedRobotWork(
  client: RobotDetailApiClient,
  user: Pick<User, 'role' | 'permissions'>,
  vin: string,
): Promise<Blocker[] | null> {
  if (!(user.permissions ?? []).includes('tracker.read')) return null
  if (user.role === 'mechanic') return (await client.mechanicRobotTickets(vin)).items
  if (user.role === 'operator') return (await client.operatorRobotTickets(vin)).items
  return (await client.trackerRobotTickets(vin)).items
}
