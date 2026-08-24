import { PageShell, Panel } from '../components/PageShell'
import { useAuth } from '../auth-context'

export function OperatorRejected() {
  const { logout } = useAuth()

  return (
    <PageShell onLogout={logout} subtitle="Доступ к кабинету оператора закрыт." title="Доступ отклонён">
      <Panel hint="Самостоятельно повторно подать заявку нельзя. Если доступ нужен — обратитесь к администратору парка." title="Что делать">
        <p>Администратор может создать нового пользователя или изменить решение вручную в базе, если это предусмотрено вашим процессом.</p>
      </Panel>
    </PageShell>
  )
}
