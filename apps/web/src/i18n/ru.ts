export const ru = {
  brand: 'РобоПарк',
  tagline: 'Платформа управления парком роботов',

  nav: {
    dashboard: 'Обзор',
    tasks: 'Работа',
    robot_search: 'Роботы',
    emergency: 'Проверка робота',
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
    brand: 'РобоПарк',
    more: 'Ещё',
    close: 'Закрыть',
  },

  auth: {
    rememberMe: 'Запомнить меня',
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

  appShell: {
    mainNavigation: 'Основная навигация',
    secondaryNavigation: 'Дополнительная навигация',
    work: 'Работа',
    skipToContent: 'К содержанию',
    collapseNavigation: 'Свернуть навигацию',
    expandNavigation: 'Развернуть навигацию',
    themeLabel: 'Тема оформления',
    themeSystem: 'Системная',
    themeLight: 'Светлая',
    themeDark: 'Тёмная',
    densityLabel: 'Плотность интерфейса',
    densityComfortable: 'Комфортная',
    densityCompact: 'Компактная',
    phoneDensity: 'На телефоне используется комфортная плотность',
    groups: {
      operations: 'Операции',
      collaboration: 'Взаимодействие',
      insights: 'Аналитика',
      administration: 'Управление',
    },
  },

  roles: {
    royal: 'Владелец',
    admin: 'Администратор',
    operator: 'Оператор',
    mechanic: 'Механик',
    driver: 'Водитель',
  } as Record<string, string>,

  screenshotGuard: {
    title: 'Создание скриншотов запрещено',
    body: 'Конфиденциальные данные парка роботов. Снимки экрана и копирование содержимого запрещены политикой безопасности.',
    confidential: 'конфиденциально',
    dismiss: 'Понятно',
    watermark: 'РобоПарк · конфиденциально',
    adminToggle: (role: string) => `Запрет скриншотов — ${role}`,
    adminHint:
      'Десктоп: перехват PrintScreen. Телефоны: постоянный водяной знак с логином (кнопки скриншота ОС перехватить нельзя). Блокировка копирования и long-press на изображениях.',
  },

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

  parks: {
    requestPark: 'Запросить парк',
    requestParkHint: 'Заявка уйдёт администратору на одобрение доступа к парку.',
    requestParkSubmit: 'Отправить заявку',
    requestParkSubmitting: 'Отправка',
    requestParkSuccess: 'Заявка отправлена администратору.',
    requestParkError: 'Не удалось отправить заявку на парк.',
    requestParkEmpty: 'Нет парков для запроса',
    requestParkEmptyHint: 'Возможно, у вас уже есть доступ ко всем активным паркам.',
    myParks: 'Мои парки',
    noAssignedParks: 'Нет назначенных парков',
    noAssignedParksHint: 'Запросите доступ к парку — заявка уйдёт администратору.',
  },

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
    register403: 'Регистрация отклонена. Проверьте общий пароль или логин.',
    register409: 'Регистрация отклонена. Проверьте общий пароль или логин.',
    register: 'Не удалось зарегистрироваться.',
    tasks503: 'Tracker не настроен — попросите администратора указать OAuth-токен.',
    tasks409: 'Задачи отключены для вашего парка (очередь или feature_blockers).',
    tasks: 'Не удалось загрузить задачи. Проверьте настройки Tracker и парка.',
    robotSearch: 'Поиск не удался. Проверьте токен Tracker.',
    emergency503: 'Интеграция проверки робота требует внимания.',
    emergency401: 'Интеграция проверки робота требует внимания.',
    sessionExpired: 'Сессия истекла. Войдите снова.',
    emergency: 'Не удалось получить данные проверки робота',
    emergencySection: 'Не удалось загрузить раздел.',
    notFound: 'Действие недоступно — перезапустите API или обновите страницу.',
    details: {
      tracker_token_not_configured:
        'Startrek не настроен — попросите администратора указать OAuth-токен.',
      tracker_issue_out_of_scope: 'Тикет вне вашей очереди или парка.',
      tracker_queue_forbidden: 'Нет доступа к этой очереди Startrek.',
      tracker_park_forbidden: 'Нет доступа к этому парку.',
      tracker_untagged_forbidden: 'Просмотр неразмеченных тикетов отключён.',
      tracker_queue_required_for_untagged:
        'Для неразмеченных укажите очередь (например SDCFLEETOPS).',
      tracker_write_disabled: 'Запись в Startrek отключена политикой.',
      tracker_upstream_error: 'Ошибка интеграции со Startrek.',
      emergency_upstream_error: 'Ошибка интеграции проверки робота.',
      invalid_robot_number: 'Некорректный номер или VIN робота.',
      password_too_short: 'Пароль слишком короткий.',
      password_too_common: 'Пароль слишком простой — выберите другой.',
      password_contains_username: 'Пароль не должен содержать логин.',
      password_complexity:
        'Пароль: минимум три из четырёх — строчные, заглавные, цифры, спецсимволы.',
      secret_key_required: 'Задайте SECRET_KEY в настройках сервера перед сохранением секретов.',
      invalid_report_kind: 'Неверный тип обращения.',
      tracker_key_required: 'Укажите ключ тикета Startrek.',
      comment_required: 'Комментарий обязателен.',
      report_not_found: 'Обращение не найдено.',
      forbidden: 'Недостаточно прав.',
      tracker_transition_invalid: 'Этот переход недоступен для тикета.',
      tracker_close_transition_not_found: 'Не найден переход для закрытия тикета.',
      tracker_attachment_empty: 'Файл пустой.',
      tracker_attachment_too_large: 'Файл слишком большой (максимум 15 МБ).',
      tracker_attachment_invalid_type: 'Можно прикреплять только изображения (JPEG, PNG, WebP, HEIC).',
      emergency_cookie_not_configured:
        'Интеграция проверки робота требует внимания.',
      emergency_cookie_invalid:
        'Интеграция проверки робота требует внимания.',
      too_many_attempts: 'Слишком много попыток. Подождите минуту.',
      emergency_vin_out_of_scope: 'Нет доступа к диагностике этого робота.',
      tasks_disabled_for_park: 'Задачи отключены: проверьте очередь Startrek и флаг blockers у парка.',
      blockers_disabled_for_park:
        'Блокеры отключены для этого парка (очередь или feature_blockers).',
      no_tracker_parks: 'Нет назначенных парков с очередью Startrek.',
      no_report_parks:
        'Нет парков для отчёта — назначьте парк или включите feature_reports.',
      cannot_delete_self: 'Нельзя удалить собственный аккаунт.',
      cannot_delete_last_royal: 'Нельзя удалить последнего владельца.',
      maintenance: 'Идут технические работы. Подождите.',
      job_in_progress: 'Уже выполняется другая операция со снимком или обновлением.',
      unexpected_kind: 'Неверный тип архива. Снимок и обновление — разные файлы.',
      tests_failed: 'Тесты пакета не прошли. Живая система не изменена.',
      confirm_required: 'Введите фразу подтверждения без ошибок.',
      archive_too_large: 'Архив слишком большой.',
      archive_required: 'Выберите ZIP-архив.',
      checksum_mismatch: 'Архив повреждён (контрольная сумма не совпала).',
      invalid_zip: 'Это не ZIP-архив РобоПарк.',
      unsafe_path: 'Архив отклонён: небезопасные пути внутри.',
      cutover_unhealthy: 'Новая версия не поднялась, выполнен откат.',
    } as Record<string, string>,
  },

  tracker: {
    loadError: 'Не удалось загрузить тикеты',
    detailsError: 'Не удалось загрузить детали тикета',
    actionsDisabled: 'Действия отключены политикой',

    listTitle: 'Тикеты',
    listEmpty: 'Тикеты не найдены. Измените фильтры.',
    selectHint: 'Выберите тикет слева, чтобы открыть карточку.',
    refresh: 'Обновить',
    showMore: 'Показать ещё',
    shown: 'Показано',
    of: 'из',

    filters: {
      title: 'Фильтры',
      queue: 'Очередь',
      park: 'Тег парка',
      status: 'Статус',
      robot: 'Робот',
      untagged: 'Только неразмеченные',
      apply: 'Применить',
      reset: 'Сбросить',
      age: 'Возраст, ч',
      presets: 'Быстрый выбор',
      presetAll: 'Все открытые',
      presetMine: 'Мои',
      presetUnassigned: 'Без исполнителя',
      presetOld: 'Старше 24 ч',
    },

    fields: {
      status: 'Статус',
      priority: 'Приоритет',
      type: 'Тип',
      queue: 'Очередь',
      assignee: 'Исполнитель',
      reporter: 'Автор',
      created: 'Создан',
      updated: 'Обновлён',
      resolution: 'Резолюция',
      tags: 'Теги',
      components: 'Компоненты',
      robot: 'Робот',
      age: 'Возраст',
      nobody: 'Не назначен',
      empty: '—',
    },

    description: 'Описание',
    descriptionEmpty: 'Описание не заполнено.',
    attachments: 'Вложения',
    comments: 'Комментарии',
    commentsEmpty: 'Комментариев пока нет.',
    history: 'История действий',
    historyEmpty: 'Действий через платформу пока нет.',
    commentPlaceholder: 'Написать комментарий…',
    commentSubmit: 'Отправить',
    commentHint: 'Подпись (логин / парк / механик) добавится автоматически.',

    attachPhoto: 'Фото неисправности',
    attachPhotoHint: 'Сфотографируйте или выберите изображение — оно появится во вложениях тикета.',
    attachPhotoPick: 'Выбрать фото',
    attachPhotoSubmit: 'Прикрепить',
    attachPhotoTooLarge: 'Файл слишком большой (максимум 15 МБ).',
    attachPhotoInvalidType: 'Можно прикреплять только изображения.',

    actions: {
      title: 'Действия',
      assign: 'Назначить',
      assignSelf: 'Назначить на себя',
      assignPlaceholder: 'Логин исполнителя',
      unassign: 'Снять исполнителя',
      close: 'Закрыть тикет',
      transitions: 'Перевести в статус',
      openInTracker: 'Открыть в Startrek',
      confirmClose: 'Закрыть тикет в Startrek?',
      done: 'Готово',
      failed: 'Не удалось выполнить действие',
    },

    robotCheck: {
      title: 'Проверка робота',
      open: 'Проверить робота',
      checking: 'Проверяем критические состояния…',
      noCritical: 'Критических состояний не обнаружено',
      found: 'Критические состояния',
      wheelsFault: 'Неисправность колёс',
    },

    priorities: {
      blocker: 'Блокер',
      critical: 'Критичный',
      major: 'Важный',
      normal: 'Обычный',
      minor: 'Незначительный',
      trivial: 'Мелочь',
    } as Record<string, string>,
  },

  emergency: {
    title: 'Проверка робота',
    subtitle: 'Живой статус, карта и данные проверки робота.',
    driverSubtitle: 'Проверка робота без проверки блокеров Tracker.',
    searchTitle: 'Робот',
    searchHint: 'Номер или VIN. Данные проверки робота обновляются каждые 2.5 с.',
    driverSearchHint: 'Короткий номер или VIN. Блокеры Tracker не проверяются.',
    robotNumber: 'Номер робота',
    robotPlaceholder: '447',
    resolve: 'Проверить',
    follow: 'Следить',
    speed: 'Скорость',
    speedUnit: 'м/с',
    charge: 'Заряд',
    battery: 'АКБ',
    battery1: 'АКБ 1',
    battery2: 'АКБ 2',
    disk: 'Диск',
    statusActive: 'Активен',
    statusOffline: 'Офлайн',
    connectionLte: 'LTE',
    connectionWire: 'Провод',
    offline: 'офлайн',
    noLink: 'нет связи',
    offlineBanner: 'ERROR: робот офлайн',
    noLinkBanner: 'ERROR: нет данных о связи',
    map: 'Карта',
    noCoords: 'Нет координат',
    enterRobot: 'Введите номер робота',
    staleHint: 'Последние данные. Обновление не удалось.',
    cookieStubTitle: 'Уже чиним',
    cookieStubBody:
      'Доступ к проверке робота временно недоступен. Администратор уже уведомлён.',
    cookieStubAdmin: 'Обновить cookie',
    sectionsHint: 'Выберите раздел для просмотра полей.',
    sectionsEmpty: 'Разделы не найдены.',
    detailEmpty: 'В разделе нет данных.',
    wheelSlots: {
      fl: 'Переднее левое колесо',
      ml: 'Среднее левое колесо',
      rl: 'Заднее левое колесо',
      fr: 'Переднее правое колесо',
      mr: 'Среднее правое колесо',
      rr: 'Заднее правое колесо',
    } as Record<string, string>,
    wheelSlotShort: {
      fl: 'Лев. пер.',
      ml: 'Лев. сред.',
      rl: 'Лев. зад.',
      fr: 'Прав. пер.',
      mr: 'Прав. сред.',
      rr: 'Прав. зад.',
      body: 'Кузов',
    } as Record<string, string>,
  },

  reports: {
    kinds: {
      ticket_question: 'Вопрос по тикету',
      ticket_close_review: 'Проверка закрытия',
      mechanic_problem: 'Проблема',
      escalation_to_admin: 'Эскалация админу',
      emergency_cookie_stale: 'Подключение проверки робота',
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
      cancel: 'Отмена',
      escalate: 'Эскалировать',
      question: 'Вопрос',
      problem: 'Проблема',
    },
  },

  ops: {
    tab: 'Снимок и обновление',
    title: 'Снимок и обновление',
    hint: 'Только владелец. Снимок — база, файлы и конфиги. Обновление — отдельный ZIP с новой версией приложения. На время операции остальные пользователи видят экран техработ.',
    snapshotTitle: 'Полный снимок',
    snapshotHint: 'Скачайте архив, чтобы перенести систему на другой хост. На пустом хосте сначала один раз поднимите Docker, войдите как владелец и восстановите снимок здесь.',
    snapshotStart: 'Создать снимок',
    snapshotDownload: 'Скачать архив',
    restoreTitle: 'Восстановление',
    restoreHint: 'Стирает текущие данные. На новом хосте нужен тот же SECRET_KEY, иначе токен Tracker и cookie диагностики робота придётся ввести заново.',
    restoreConfirmLabel: 'Фраза подтверждения',
    restoreSubmit: 'Восстановить',
    updateTitle: 'Обновление системы',
    updateHint: 'Загрузите пакет обновления (не снимок). Система проверит архив, прогонит тесты на копии и только потом выложит версию. Перед выкладкой автоматически снимается снимок.',
    updateSubmit: 'Обновить',
    updateConfirmLabel: 'Фраза подтверждения',
    chooseZip: 'ZIP-архив',
    jobIdle: 'Операций нет',
    restartHint: 'Файлы записаны. Пересоберите контейнеры на хосте: docker compose up -d --build (или дождитесь ops-agent).',
    secretKeyHint: 'SECRET_KEY из host.env входит в снимок. Без того же ключа зашифрованные секреты на новом хосте не прочитаются.',
  },

  maintenance: {
    title: 'Технические работы',
    body: 'Владелец обновляет систему или снимает резервную копию. Кабинет временно недоступен.',
    snapshot: 'Создаётся снимок системы',
    restore: 'Восстанавливаются данные',
    update: 'Устанавливается обновление',
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
