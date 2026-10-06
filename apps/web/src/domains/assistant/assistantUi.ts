const ERROR_MESSAGES: Record<string, string> = {
  ai_action_in_progress: 'Действие уже выполняется. Дождитесь результата перед отменой или удалением разговора.',
  ai_action_uncertain: 'Связь прервалась во время действия. Проверьте его результат перед повторной командой.',
  ai_confirmation_expired: 'Время подтверждения истекло. Попросите помощника подготовить действие заново.',
  ai_confirmation_changed: 'Параметры подтверждения не совпали. Обновите разговор.',
  ai_action_changed: 'Объект изменился после подготовки действия. Попросите помощника проверить свежие данные.',
  ai_tool_limit: 'Достигнут предел действий на одно сообщение. Проверьте результаты и задайте следующий шаг.',
  ai_system_api_interactive: 'Эту операцию нужно выполнить в соответствующем разделе сайта с обычной проверкой доступа.',
  ai_system_api_forbidden: 'Для этой операции у вашей учётной записи нет доступа.',
  ai_system_api_operation_not_found: 'Операция API не найдена. Попросите помощника заново найти доступную операцию.',
  ai_system_api_result_too_large: 'Ответ сервиса слишком большой. Уточните парк, робота или период проверки.',
  ai_system_api_path_invalid: 'Не удалось определить объект операции. Уточните номер робота или задачи.',
  ai_system_api_secret_forbidden: 'Секреты вводятся только в настройках соответствующего сервиса.',
  sdc_inventory_token_not_configured: 'Для Inventory нужен корпоративный OAuth в настройках интеграции Tracker.',
  sdc_inventory_access_denied: 'Корпоративный OAuth не получил доступ к SDC Inventory.',
  sdc_inventory_not_found: 'Робот не найден в SDC Inventory. Проверьте его номер.',
  sdc_inventory_unavailable: 'SDC Inventory сейчас недоступен. Повторите проверку позже.',
  sdc_inventory_busy: 'Сейчас выполняется много проверок Inventory. Повторите немного позже.',

  ai_context_too_large: 'Запрос слишком большой. Уменьшите запрос или разделите его на несколько сообщений.',
  ai_sources_changed: 'Источники изменились. Задайте вопрос заново, чтобы получить актуальный ответ.',
  ai_not_ready: 'Помощник ещё не готов. Проверьте модель и повторите позже.',
  ai_disabled: 'Помощник выключен. Администратор может включить его в настройках.',
  disabled: 'Помощник выключен. Администратор может включить его в настройках.',
  ai_runtime_busy: 'Сейчас все исполнители заняты. Повторите запрос немного позже.',
  ai_queue_full: 'Очередь помощника заполнена. Повторите попытку немного позже.',
  ai_finalize_busy: 'Не удалось сохранить результат: помощник занят системной операцией. Повторите запрос после её завершения.',
  queue_full: 'Очередь помощника заполнена. Повторите попытку немного позже.',
  runtime_unavailable: 'Среда запуска помощника недоступна. Проверьте установку и состояние модели.',
  worker_interrupted: 'Обработка была прервана. Отправьте запрос ещё раз.',
  issue_unavailable: 'Не удалось получить данные задачи. Проверьте доступ к ней.',
  issue_park_mismatch: 'Задача относится к другому парку. Откройте помощника из нужного парка.',
  script_test_required: 'Сначала проверьте текущую ревизию скрипта.',
  revision_conflict: 'Данные уже изменились. Обновите страницу и повторите правку.',
  connector_url_invalid: 'Проверьте HTTPS-адрес подключения и допустимый шаблон пути.',
}

const STATUS_MESSAGES: Record<string, string> = {
  disabled: ERROR_MESSAGES.disabled, ai_disabled: ERROR_MESSAGES.ai_disabled,
  agx_required: 'Требуется NVIDIA AGX Orin.', p3701_required: 'Требуется NVIDIA AGX Orin P3701.',
  tegra234_required: 'Требуется платформа NVIDIA Tegra 234.', memory_below_24gib: 'Недостаточно оперативной памяти: требуется не менее 24 ГиБ.',
  storage: 'Хранилище модели недоступно или не готово.', install_failed: 'Подготовка среды завершилась с ошибкой.',
  model_missing: 'Файл модели не найден.', runtime_unavailable: ERROR_MESSAGES.runtime_unavailable,
  runtime_model_mismatch: 'Выбранная модель несовместима. Проверьте регистрацию и профиль модели.',
  installing: 'Готовим среду запуска без скачивания модели. Страница обновится автоматически.',
  awaiting_model: 'Ожидается обученная модель. Ответы ИИ выключены; настройки и инструменты можно подготовить заранее.',
  startup_failed: 'Модель не запустилась. Проверьте выбранный файл и журнал службы.',
  model_invalid: 'Файл модели не прошёл проверку. Повторите регистрацию правильного GGUF.',
  starting: 'Запускаем локальную модель.', not_installed: 'Локальная модель ещё не установлена.',
  cuda_unavailable: 'CUDA недоступна. Проверьте драйвер и конфигурацию устройства.',
  smoke_failed: 'Модель не прошла проверочный запуск. Повторите установку или проверьте журнал.',
}

function translatedMessage(message: string, messages: Record<string, string>): string | null {
  for (const [code, text] of Object.entries(messages)) if (message === code || message.includes(code)) return text
  return null
}

export function assistantErrorText(error: unknown): string {
  const message = error instanceof Error ? error.message : typeof error === 'string' ? error : ''
  return translatedMessage(message, ERROR_MESSAGES) ?? (message || 'Не удалось выполнить действие. Попробуйте ещё раз.')
}

export function statusReasonText(reason: string | null): string {
  return reason ? translatedMessage(reason, STATUS_MESSAGES) ?? `Локальный помощник недоступен (${reason}).` : 'Локальный помощник недоступен.'
}
