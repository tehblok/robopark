import { ApiError, type DiagnosticRuleCreate } from '../../api'

export type Draft = Required<Omit<DiagnosticRuleCreate, 'sort_order'>>

export const emptyDraft: Draft = { source_path: '', match_kind: 'exact', pattern: '', example: '', title: '', description: '', severity: 'warning', part: '', preferred_view: 'front', x: .5, y: .5, indicator: 'point', is_enabled: true }

export function errorText(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 401) return 'Сессия истекла. Войдите снова.'
    if (error.status === 403) return 'Нет доступа к каталогу ошибок. Обратитесь к администратору.'
    if (error.detail === 'unsupported_diagnostic_regex') return 'Этот шаблон не поддерживается или слишком сложен. Упростите регулярное выражение.'
    if (error.detail === 'invalid_diagnostic_regex') return 'Некорректное регулярное выражение. Проверьте скобки и специальные символы.'
    if (error.detail === 'diagnostic_preview_source_too_large') return 'Пример требует слишком большого массива. Уменьшите индексы в пути источника.'
    if (error.detail === 'invalid_diagnostic_source_path') return 'Проверьте путь источника: используйте имена полей и индексы через точку.'
    if (error.detail === 'unknown_sample_requires_observation') return 'Повторите проверку робота для получения исходного сигнала.'
    if (error.detail === 'unknown_rule_does_not_match') return 'Правило не распознаёт исходный сигнал. Проверьте шаблон и повторите проверку примера.'
    if (error.detail === 'diagnostic_sample_catalog_too_large') return 'Каталог слишком велик для полной проверки пересечений. Допускается до 100 включённых правил.'
    if (error.status === 422) return 'Проверьте поля правила и пример: сервер не смог их обработать.'
    if (error.detail === 'diagnostic_unknown_already_mapped') return 'Эту ошибку уже разметили. Откройте связанное правило.'
    if (error.status === 409) return 'Правило конфликтует с каталогом. Обновите список и повторите сохранение.'
  }
  return 'Не удалось выполнить запрос. Повторите попытку.'
}
