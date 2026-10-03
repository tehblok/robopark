export function formatSlaRemaining(hours: number | null): string {
  if (hours === null || !Number.isFinite(hours)) return '—'
  const minutes = Math.ceil(Math.max(0, Math.min(5, hours)) * 60 - 1e-8)
  return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, '0')}`
}
