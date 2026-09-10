import { type InventoryPart } from '../../api'

export function InventoryLabels({ parts }: { parts: InventoryPart[] }) {
  return <section aria-hidden="true" className="inventory-print-sheet">
    {parts.map(part => <article className="inventory-print-label" key={part.id}>
      <strong>{part.name}</strong><span>Артикул: {part.article}</span><b>{part.location}</b>
    </article>)}
  </section>
}
