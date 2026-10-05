import type { KnowledgeImportDocument } from './assistantApi'

const ERROR_MESSAGES: Record<string, string> = {
  ai_action_in_progress: 'Действие уже выполняется. Дождитесь результата перед отменой или удалением разговора.',
  ai_action_uncertain: 'Связь прервалась во время действия. Проверьте его результат перед повторной командой.',
  ai_confirmation_expired: 'Время подтверждения истекло. Попросите помощника подготовить действие заново.',
  ai_confirmation_changed: 'Параметры подтверждения не совпали. Обновите разговор.',
  ai_action_changed: 'Объект изменился после подготовки действия. Попросите помощника проверить свежие данные.',
  ai_tool_limit: 'Достигнут предел действий на одно сообщение. Проверьте результаты и задайте следующий шаг.',

  ai_context_too_large: 'Запрос слишком большой. Уменьшите запрос или разделите его на несколько сообщений.',
  ai_sources_changed: 'Источники изменились. Задайте вопрос заново, чтобы получить актуальный ответ.',
  ai_not_ready: 'Помощник ещё не готов. Проверьте модель и повторите позже.',
  ai_disabled: 'Помощник выключен. Администратор может включить его в настройках.',
  disabled: 'Помощник выключен. Администратор может включить его в настройках.',
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
  storage: 'Хранилище модели недоступно или не готово.', install_failed: 'Установка модели завершилась с ошибкой.',
  model_missing: 'Файл модели не найден.', runtime_unavailable: ERROR_MESSAGES.runtime_unavailable,
  installing: 'Устанавливаем локальную модель. Страница обновится автоматически.',
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

const MAX_IMPORT_REQUEST_BYTES = 4 * 1024 * 1024

export function createKnowledgeImportBatches(documents: KnowledgeImportDocument[], parkId: number | null, activateManuals: boolean, activateUnverified: boolean): KnowledgeImportDocument[][] {
  const encoder = new TextEncoder()
  const batches: KnowledgeImportDocument[][] = []
  let current: KnowledgeImportDocument[] = []
  const size = (items: KnowledgeImportDocument[]) => encoder.encode(JSON.stringify({ documents: items, park_id: parkId, activate_manuals: activateManuals, activate_unverified: activateUnverified })).byteLength
  for (const document of documents) {
    const next = [...current, document]
    if (next.length <= 100 && size(next) <= MAX_IMPORT_REQUEST_BYTES) { current = next; continue }
    if (!current.length || size([document]) > MAX_IMPORT_REQUEST_BYTES) throw new Error(`Документ «${document.title}» не помещается в пакет 4 МиБ`)
    batches.push(current); current = [document]
  }
  if (current.length) batches.push(current)
  return batches
}
