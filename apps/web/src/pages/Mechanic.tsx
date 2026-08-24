import { Link } from 'react-router-dom'
import { PageShell, Panel } from '../components/PageShell'
import { useAuth } from '../auth-context'

const tools = [
  {
    href: '/mechanic/tasks',
    title: 'Задачи парка',
    text: 'Открытые blocker-ы вашего парка в Tracker с фильтрами по статусу.',
  },
  {
    href: '/mechanic/robot-search',
    title: 'Поиск робота',
    text: 'Все тикеты по номеру робота или ключу задачи без ограничения парком.',
  },
  {
    href: '/mechanic/tracker',
    title: 'Рабочий стол Tracker',
    text: 'Карточка тикета: комментарии, назначение, переходы и закрытие.',
  },
  {
    href: '/mechanic/emergency',
    title: 'Emergency',
    text: 'Проверка робота по номеру: VIN, разделы телеметрии и диагностики.',
  },
] as const

export function Mechanic() {
  const { logout, user } = useAuth()
  const park = user?.parks?.[0]

  return (
    <PageShell
      onLogout={logout}
      standalone
      subtitle={
        park
          ? `Парк: ${park.name} (${park.tag}). Выберите инструмент для работы.`
          : 'Инструменты механика.'
      }
      title="Кабинет механика"
    >
      <div className="tool-grid">
        {tools.map((tool) => (
          <article className="tool-card" key={tool.href}>
            <h3>{tool.title}</h3>
            <p>{tool.text}</p>
            <Link to={tool.href}>Открыть →</Link>
          </article>
        ))}
      </div>

      <Panel hint="Если инструмент не работает, проверьте в админке: OAuth Startrek (st.yandex-team.ru), cookie Emergency и tracker_queue у парка." title="Подсказка">
        <p>Задачи доступны только при включённом feature_blockers и указанной очереди Tracker.</p>
      </Panel>
    </PageShell>
  )
}
