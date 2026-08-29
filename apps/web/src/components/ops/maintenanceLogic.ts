export type MaintenanceStatus = {
  active: boolean
  kind: string | null
  operator: boolean
}

export function shouldShowMaintenance(status: MaintenanceStatus | null): boolean {
  return Boolean(status?.active && !status.operator)
}

export function maintenanceKindLabel(
  kind: string | null,
  labels: Record<string, string>,
  fallback: string,
): string {
  if (!kind) return fallback
  return labels[kind] ?? fallback
}
