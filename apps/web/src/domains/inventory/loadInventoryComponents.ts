import type { api, InventoryCatalogComponent } from '../../api'

type ComponentCatalogApi = Pick<typeof api, 'inventoryCatalogComponents'>

const PAGE_SIZE = 200
export const INVENTORY_COMPONENTS_INCOMPLETE = 'Не удалось полностью загрузить список компонент.'

export async function loadInventoryComponents(apiClient: ComponentCatalogApi, parkId: number): Promise<InventoryCatalogComponent[]> {
  const byId = new Map<number, InventoryCatalogComponent>()
  let offset = 0
  while (true) {
    const previousSize = byId.size
    const response = await apiClient.inventoryCatalogComponents(parkId, { limit: PAGE_SIZE, offset })
    response.items.forEach(item => byId.set(item.id, item))
    if (byId.size >= response.total) break
    if (!response.items.length || byId.size === previousSize) throw new Error(INVENTORY_COMPONENTS_INCOMPLETE)
    offset += response.items.length
  }
  return [...byId.values()].sort((left, right) => left.name.localeCompare(right.name, 'ru') || left.id - right.id)
}
