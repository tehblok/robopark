export type InventoryDocumentStatus = 'draft' | 'posted' | 'cancelled'
export type InventoryStockFilter = 'in_stock' | 'below_minimum' | 'without_location'

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

export type InventoryCatalogSearchItem = InventoryCatalogPart & {
  component_name: string
  quantity: number
  minimum_quantity: number
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
  quantity: number
  minimum_quantity: number
  location: string | null
  is_active: boolean
  version: number
}

export type InventoryReceiptLineInput = {
  catalog_part_id: number
  quantity: number
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
  quantity: number
  note: string | null
}

export type InventoryReceipt = {
  id: number
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
  actual_quantity: number
  comment?: string | null
}

export type InventoryCountLine = {
  id: number
  catalog_part_id: number
  expected_quantity: number
  actual_quantity: number | null
  difference: number | null
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
}

export type InventoryExportParams =
  | { parkId: number; scope?: never; format: 'csv' | 'xlsx' }
  | { parkId?: never; scope: 'all'; format: 'csv' | 'xlsx' }
