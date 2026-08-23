import { PageShell, Panel } from '../components/PageShell'

export function MechanicNoPark() {
  return (
    <PageShell subtitle="Без привязки к парку инструменты недоступны." title="Парк не назначен">
      <Panel hint="Администратор должен создать или обновить учётную запись механика и привязать ровно один активный парк." title="Что делать">
        <p>После назначения парка выйдите и войдите снова — откроется полный кабинет.</p>
      </Panel>
    </PageShell>
  )
}
