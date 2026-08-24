import { Link } from 'react-router-dom'
import { PageShell, Panel } from '../components/PageShell'
import { useAuth } from '../auth-context'

const tools = [
  {
    href: '/operator/blockers',
    title: 'Блокеры',
    text: 'Открытые blocker по выбранному парку с фильтрами статуса.',
  },
  {
    href: '/operator/robot-search',
    title: 'Поиск робота',
    text: 'Тикеты по номеру робота или ключу задачи по вашим паркам.',
  },
  {
    href: '/operator/now-report',
    title: 'Сейчас по Tracker',
    text: 'Сводка: блокеры, бэклог, статусы, сегодня пришло / сделано.',
  },
  {
    href: '/operator/tracker',
    title: 'Рабочий стол Tracker',
    text: 'Карточка тикета: комментарии, назначение, переходы и закрытие.',
  },
  {
    href: '/operator/emergency',
    title: 'Emergency',
    text: 'Проверка робота по номеру: VIN, телеметрия и диагностика.',
  },
  {
    href: '/operator/parks',
    title: 'Мои парки',
    text: 'Заявки на доступ и история запросов.',
  },
] as const

export function Operator() {
  const { logout } = useAuth()

  return (
    <PageShell
      onLogout={logout}
      standalone
      subtitle="Выберите инструмент для работы с парками и Tracker."
      title="Кабинет оператора"
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

      <Panel hint="Если инструмент не работает, проверьте в админке: OAuth Tracker, cookie Emergency и tracker_queue у парка." title="Подсказка">
        <p>Блокеры и отчёты доступны только при включённом feature_blockers и указанной очереди Tracker.</p>
      </Panel>
    </PageShell>
  )
}
