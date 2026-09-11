import { createPortal } from 'react-dom'

export type InventoryLabelPart = {
  id: number
  name: string
  article: string
  location: string | null
}

export function InventoryLabels({ parts }: { parts: InventoryLabelPart[] }) {
  if (!parts.length || typeof document === 'undefined') return null
  return createPortal(<section aria-hidden="true" className="inventory-print-sheet">
    {parts.map(part => <article className="inventory-print-label" key={part.id}>
      <strong>{part.name}</strong><span>Артикул: {part.article}</span><b>{part.location || 'Место не указано'}</b>
    </article>)}
  </section>, document.body)
}
