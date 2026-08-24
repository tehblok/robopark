export const ru = {
  brand: 'Робопарк Сервис',
  tagline: 'Платформа управления парком роботов',

  nav: {
    dashboard: 'Дашборд',
    tasks: 'Задачи',
    robot_search: 'Поиск по роботу',
    emergency: 'Проверка по роботу',
    map: 'Карта',
    analytics: 'Аналитика',
    reports: 'Репорты',
    learning: 'Обучение',
    help: 'Помощь',
    soon: 'Скоро',
    park: 'Парк',
    user: 'Пользователь',
    themeLight: 'Светлая тема',
    themeDark: 'Тёмная тема',
    admin: 'Администрирование',
    brand: 'Робопарк Сервис',
  },

  loading: 'Загрузка…',
  signOut: 'Выйти',
  back: 'Назад',
  save: 'Сохранить',
  create: 'Создать',
  search: 'Найти',
  approve: 'Одобрить',
  reject: 'Отклонить',
  activate: 'Активировать',
  deactivate: 'Деактивировать',
  active: 'активен',
  inactive: 'неактивен',
  empty: 'Пока ничего нет',

  theme: {
    light: 'Светлая тема',
    dark: 'Тёмная тема',
  },

  roles: {
    royal: 'Владелец',
    admin: 'Администратор',
    operator: 'Оператор',
    mechanic: 'Механик',
  } as Record<string, string>,

  accessStatus: {
    pending: 'ожидает',
    approved: 'одобрен',
    rejected: 'отклонён',
  } as Record<string, string>,

  requestStatus: {
    pending: 'на рассмотрении',
    approved: 'одобрена',
    rejected: 'отклонена',
  } as Record<string, string>,

  taskFilters: {
    all: 'Все',
    moving: 'Перемещение',
    queued: 'В очереди',
    waiting_team: 'Смежники',
    waiting_parts: 'Запчасти',
    other: 'Прочее',
  } as Record<string, string>,

  errors: {
    generic: 'Не удалось выполнить действие. Попробуйте ещё раз.',
    load: 'Не удалось загрузить данные.',
    login: 'Неверный логин или пароль.',
    register403: 'Неверный общий пароль регистрации.',
    register409: 'Такой логин уже занят.',
    register: 'Не удалось зарегистрироваться.',
    tasks503: 'Tracker не настроен — попросите администратора указать OAuth-токен.',
    tasks409: 'Задачи отключены для вашего парка (очередь или feature_blockers).',
    tasks: 'Не удалось загрузить задачи. Проверьте настройки Tracker и парка.',
    robotSearch: 'Поиск не удался. Проверьте токен Tracker.',
    emergency503: 'Emergency cookie не задан — обратитесь к администратору.',
    emergency401: 'Cookie Emergency просрочен или недействителен.',
    emergency: 'Не удалось получить данные Emergency.',
    emergencySection: 'Не удалось загрузить раздел.',
    details: {
      blockers_disabled_for_park:
        'Блокеры отключены для этого парка (очередь или feature_blockers).',
      no_tracker_parks: 'Нет назначенных парков с очередью Tracker.',
      no_report_parks:
        'Нет парков для отчёта — назначьте парк или включите feature_reports.',
      tracker_token_not_configured:
        'Tracker не настроен — попросите администратора указать OAuth-токен.',
      tracker_issue_out_of_scope: 'Тикет вне вашей очереди или парка.',
      tracker_queue_forbidden: 'Нет доступа к этой очереди Tracker.',
      tracker_park_forbidden: 'Нет доступа к этому парку.',
      tracker_untagged_forbidden: 'Просмотр неразмеченных тикетов отключён.',
      tracker_write_disabled: 'Запись в Tracker отключена политикой.',
      tracker_upstream_error: 'Ошибка интеграции с Tracker.',
      tracker_transition_invalid: 'Этот переход недоступен для тикета.',
      tracker_close_transition_not_found: 'Не найден переход для закрытия тикета.',
      emergency_cookie_not_configured: 'Cookie Emergency не задан в настройках администратора.',
      emergency_cookie_invalid: 'Cookie Emergency просрочен или недействителен.',
      tasks_disabled_for_park: 'Задачи отключены: проверьте очередь Tracker и флаг blockers у парка.',
    } as Record<string, string>,
  },

  tracker: {
    loadError: 'Не удалось загрузить тикеты',
    detailsError: 'Не удалось загрузить детали тикета',
    actionsDisabled: 'Действия отключены политикой',
  },

  emergency: {
    title: 'Emergency',
    subtitle: 'Номер робота → VIN → разделы данных Emergency API.',
    searchTitle: 'Поиск робота',
    searchHint: 'Нужен cookie Emergency в настройках администратора.',
    robotNumber: 'Номер робота',
    robotPlaceholder: '447',
    resolve: 'Проверить',
    refresh: 'Обновить данные',
    sectionsHint: 'Выберите раздел для просмотра полей.',
    sectionsEmpty: 'Разделы не найдены.',
    detailEmpty: 'В разделе нет данных.',
  },

  reports: {
    kinds: {
      ticket_question: 'Вопрос по тикету',
      ticket_close_review: 'Проверка закрытия',
      mechanic_problem: 'Проблема',
      escalation_to_admin: 'Эскалация админу',
    } as Record<string, string>,
    statuses: {
      open: 'Открыт',
      returned: 'Возвращён',
      done: 'Готово',
    } as Record<string, string>,
    actions: {
      return: 'Вернуть',
      done: 'Готово',
      close: 'Закрыть',
      escalate: 'Эскалировать',
      question: 'Вопрос',
      problem: 'Проблема',
    },
  },
}

export function roleLabel(role: string) {
  return ru.roles[role] ?? role
}

export function accessStatusLabel(status: string) {
  return ru.accessStatus[status] ?? status
}

export function requestStatusLabel(status: string) {
  return ru.requestStatus[status] ?? status
}

export function taskFilterLabel(key: string) {
  return ru.taskFilters[key] ?? key
}

export function reportKindLabel(kind: string) {
  return ru.reports.kinds[kind] ?? kind
}

export function reportStatusLabel(status: string) {
  return ru.reports.statuses[status] ?? status
}
