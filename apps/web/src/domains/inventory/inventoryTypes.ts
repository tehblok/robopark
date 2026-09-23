export type InventoryDocumentStatus = 'draft' | 'posted' | 'cancelled'
export type InventoryStockFilter = 'in_stock' | 'below_minimum' | 'without_location'
export type InventoryInt64 = `${bigint}`
export const INVENTORY_INT64_MAX = 9_223_372_036_854_775_807n

export function inventoryInt64Compare(left: InventoryInt64, right: InventoryInt64): number {
  const leftValue = BigInt(left)
  const rightValue = BigInt(right)
  return leftValue < rightValue ? -1 : leftValue > rightValue ? 1 : 0
}

export function isInventoryInt64(value: string): value is InventoryInt64 {
  return /^-?\d+$/.test(value)
}

export function isInventoryQuantity(value: string): value is InventoryInt64 {
  return /^\d+$/.test(value) && BigInt(value) <= INVENTORY_INT64_MAX
}

export function inventoryQuantityError(value: string): string | undefined {
  if (!/^\d+$/.test(value)) return 'Целое неотрицательное число'
  if (BigInt(value) > INVENTORY_INT64_MAX) return 'Не больше 9223372036854775807'
  return undefined
}

export function isPositiveInventoryQuantity(value: string): value is InventoryInt64 {
  return isInventoryQuantity(value) && BigInt(value) > 0n
}

export type InventoryCatalogComponent = {
  id: number
  name: string
  is_active: boolean
  has_photo: boolean
}

export type InventoryCatalogPart = {
  id: number
  component_id: number
  name: string
  article: string
  is_active: boolean
  has_photo: boolean
}

export type InventoryCatalogDeleteSummary = {
  deleted_part_count: number
  deleted_part_ids: number[]
  matched_deleted_count: number
  deleted_parts: Array<Pick<InventoryCatalogPart, 'id' | 'name' | 'article' | 'is_active'> & {
    component_is_active: boolean
    merged_into_part_id: number | null
  }>
}

export type InventoryCatalogSearchItem = InventoryCatalogPart & {
  component_name: string
  quantity: InventoryInt64
  minimum_quantity: InventoryInt64
  location: string | null
  stock_is_active: boolean
}

export type InventoryPageEnvelope<T> = {
  items: T[]
  limit: number
  offset: number
  total: number
}

export type InventoryStockView = {
  park_id: number
  catalog_part_id: number
  quantity: InventoryInt64
  minimum_quantity: InventoryInt64
  location: string | null
  is_active: boolean
  version: InventoryInt64
}

export type InventoryReceiptLineInput = {
  catalog_part_id: number
  quantity: InventoryInt64
  note?: string | null
}

export type InventoryReceiptInput = {
  supplier?: string | null
  document_number?: string | null
  received_on: string
  comment?: string | null
  lines: InventoryReceiptLineInput[]
}

export type InventoryReceiptLine = {
  id: number
  catalog_part_id: number
  catalog_part_name: string
  catalog_part_article: string
  catalog_component_id: number
  catalog_component_name: string
  quantity: InventoryInt64
  note: string | null
}

export type InventoryReceipt = {
  id: number
  revision: string
  park_id: number
  supplier: string | null
  document_number: string | null
  received_on: string
  comment: string | null
  status: InventoryDocumentStatus
  created_by: number
  posted_by: number | null
  created_at: string
  posted_at: string | null
  lines: InventoryReceiptLine[]
}

export type InventoryCountScope =
  | { kind: 'all'; component_id?: never }
  | { kind: 'component'; component_id: number }

export type InventoryCountLineInput = {
  catalog_part_id: number
  actual_quantity: InventoryInt64
  comment?: string | null
}

export type InventoryCountLine = {
  id: number
  catalog_part_id: number
  catalog_part_name: string
  catalog_part_article: string
  catalog_component_id: number
  catalog_component_name: string
  expected_quantity: InventoryInt64
  actual_quantity: InventoryInt64 | null
  difference: InventoryInt64 | null
  comment: string | null
}

export type InventoryCount = {
  id: number
  park_id: number
  name: string
  status: InventoryDocumentStatus
  created_by: number
  posted_by: number | null
  created_at: string
  posted_at: string | null
  lines: InventoryCountLine[]
}

export type InventoryListParams = {
  query?: string
  limit?: number
  offset?: number
}

export type InventorySearchParams = InventoryListParams & {
  parkId: number
  componentId?: number
  stockFilter?: InventoryStockFilter
  mode?: 'active' | 'archived' | 'all'
}

export type InventoryExportParams =
  | { parkId: number; scope?: never; format: 'csv' | 'xlsx' }
  | { parkId?: never; scope: 'all'; format: 'csv' | 'xlsx' }

export type InventoryDuplicateErrorDetail =
  | { code: 'inventory_article_exists'; existing_part_id: number }
  | { code: 'inventory_component_exists'; existing_component_id: number }

export type InventoryCountStaleErrorDetail = {
  code: 'inventory_count_stale'
  conflicts: Array<{
    catalog_part_id: number
    expected_quantity: InventoryInt64
    current_quantity: InventoryInt64
    affected_lines?: Array<{ count_line_id: number; catalog_part_id: number }>
  }>
}

export type InventoryApiErrorDetail =
  | InventoryDuplicateErrorDetail
  | { code: 'inventory_out_of_stock'; current_quantity: InventoryInt64 }
  | InventoryCountStaleErrorDetail
  | { code: 'inventory_park_required'; park_ids: number[] }
  | { code: string; [key: string]: unknown }
