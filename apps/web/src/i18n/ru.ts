export const ru = {
  brand: 'Robopark',
  tagline: 'Платформа управления парком роботов',

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
