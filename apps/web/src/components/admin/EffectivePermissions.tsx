import type { PermissionCatalogItem } from '../../api'

export function EffectivePermissions({ permissions, catalog }: {
  permissions: ReadonlySet<string>
  catalog: PermissionCatalogItem[]
}) {
  return (
    <section aria-label="Итоговые доступы" aria-live="polite" className="rp-permissions-preview">
      <h3>Итоговые доступы · {permissions.size}</h3>
      <p className="panel-hint">Набор после сохранения изменений.</p>
      {permissions.size ? <ul>{[...permissions].sort().map(key => (
        <li key={key}>{catalog.find(item => item.key === key)?.label ?? key}</li>
      ))}</ul> : <p>Доступы не выбраны.</p>}
    </section>
  )
}
