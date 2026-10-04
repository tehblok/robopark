import type { InventoryCatalogSearchItem } from '../../api'

export function retainInventoryPartRecords(
  current: InventoryCatalogSearchItem[],
  selected: ReadonlySet<number>,
  previous: ReadonlyMap<number, InventoryCatalogSearchItem>,
): Map<number, InventoryCatalogSearchItem> {
  const retained = new Map(current.map(item => [item.id, item]))
  selected.forEach(id => {
    const item = previous.get(id)
    if (item && !retained.has(id)) retained.set(id, item)
  })
  return retained
}
