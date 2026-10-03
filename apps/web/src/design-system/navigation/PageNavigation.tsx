import { Button } from '../actions/Button'
import './PageNavigation.css'

export function PageNavigation({
  page,
  hasNext,
  onChange,
  onReset,
  label = 'Страницы',
}: {
  page: number
  hasNext: boolean
  onChange: (page: number) => void
  onReset?: () => void
  label?: string
}) {
  if (page <= 1 && !hasNext && !onReset) return null
  return <nav aria-label={label} className="rp-page-navigation">
    <Button disabled={page <= 1} onClick={() => onChange(page - 1)} size="compact" variant="secondary">Предыдущая страница</Button>
    <span aria-live="polite">Страница {page}</span>
    <Button disabled={!hasNext} onClick={() => onChange(page + 1)} size="compact" variant="secondary">Следующая страница</Button>
    {onReset && <Button onClick={onReset} size="compact" variant="ghost">Показать новые</Button>}
  </nav>
}
