import { type InventoryPart } from '../../api'
import { createPortal } from 'react-dom'

export function InventoryLabels({ parts }: { parts: InventoryPart[] }) {
  if (!parts.length || typeof document === 'undefined') return null
  return createPortal(<section aria-hidden="true" className="inventory-print-sheet">
    {parts.map(part => <article className="inventory-print-label" key={part.id}>
      <strong>{part.name}</strong><span>Артикул: {part.article}</span><b>{part.location}</b>
    </article>)}
  </section>, document.body)
}
