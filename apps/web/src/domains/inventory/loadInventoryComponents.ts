import type { api, InventoryCatalogComponent } from '../../api'

type ComponentCatalogApi = Pick<typeof api, 'inventoryCatalogComponents'>

const PAGE_SIZE = 200
const MAX_PAGES = 20

export async function loadInventoryComponents(apiClient: ComponentCatalogApi, parkId: number): Promise<InventoryCatalogComponent[]> {
  const byId = new Map<number, InventoryCatalogComponent>()
  let offset = 0
  for (let page = 0; page < MAX_PAGES; page += 1) {
    const response = await apiClient.inventoryCatalogComponents(parkId, { limit: PAGE_SIZE, offset })
    response.items.forEach(item => byId.set(item.id, item))
    if (!response.items.length || response.items.length < PAGE_SIZE || offset + response.items.length >= response.total) break
    offset += PAGE_SIZE
  }
  return [...byId.values()].sort((left, right) => left.name.localeCompare(right.name, 'ru') || left.id - right.id)
}
