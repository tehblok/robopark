export const REGISTER_ROLES = ['operator', 'mechanic', 'driver'] as const

export type RegisterRole = (typeof REGISTER_ROLES)[number]
