import { PageShell, Panel } from '../components/PageShell'
import { useAuth } from '../auth-context'

export function OperatorPending() {
  const { logout } = useAuth()

  return (
    <PageShell onLogout={logout} subtitle="Регистрация прошла успешно." title="Ожидание одобрения">
      <Panel hint="Администратор или владелец должен одобрить доступ и назначить хотя бы один активный парк. После этого откроется полный кабинет оператора." title="Что дальше">
        <p>Вы можете выйти и зайти позже — статус сохранится. Повторная регистрация с тем же логином недоступна.</p>
      </Panel>
    </PageShell>
  )
}
