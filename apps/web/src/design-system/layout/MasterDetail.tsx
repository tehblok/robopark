import type { ReactElement, ReactNode } from 'react'
import { Button } from '../actions/Button'
import './MasterDetail.css'

export type MasterDetailProps = {
  list: ReactNode
  detail: ReactNode
  detailOpen: boolean
  onBack: () => void
}

export function MasterDetail({
  list,
  detail,
  detailOpen,
  onBack,
}: MasterDetailProps): ReactElement {
  return (
    <div className="rp-master-detail" data-detail-open={detailOpen}>
      <section className="rp-master-detail__list">{list}</section>
      <section className="rp-master-detail__detail">
        <div className="rp-master-detail__detail-actions">
          <Button className="rp-master-detail__back" onClick={onBack} type="button" variant="ghost">
            Назад к списку
          </Button>
        </div>
        {detail}
      </section>
    </div>
  )
}
