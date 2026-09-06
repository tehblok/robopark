import type { DiagnosticSampleResult } from './diagnosticSampleApi'

const outcomes = { matched: 'Совпало', missed: 'Не совпало', skipped: 'Пропущено' }
const reasons = { legacy: 'Нет сохранённого оригинала. Нужен новый сигнал от робота.', unusable: 'Образец повреждён, содержит чувствительные данные или превышает ограничения.', budget: 'Достигнут предел времени или сложности проверки.' }

export function DiagnosticSampleSummary({ result }: { result: DiagnosticSampleResult }) {
  return <section className="rp-diagnostic-preview" aria-label="Проверка собранных ошибок">
    <p role="status">Совпало: {result.matched} · Не совпало: {result.missed} · Пропущено: {result.skipped}</p>
    <p>Пересечения с действующими правилами: {result.overlapping}. Это распознавание одного образца; разные правила могут относиться к разным его частям.</p>
    {result.has_more ? <p>Показаны последние {result.limit} образцов. В хранилище есть более ранние сигналы.</p> : null}
    {result.budget_exhausted ? <p role="alert">Проверка завершена частично: достигнут предел времени или сложности. Пропущенные образцы не считаются несовпадениями.</p> : null}
    {result.invalid_rule_ids.length ? <p role="alert">Не удалось проверить пересечения с правилами: {result.invalid_rule_ids.map(id => `#${id}`).join(', ')}.</p> : null}
    {!result.items.length ? <p>Собранных образцов пока нет.</p> : <details><summary>Образцы и пересечения</summary><ul className="rp-diagnostic-sample-list">
      {result.items.map(item => <li key={item.id}><strong>Образец #{item.id}</strong> · {outcomes[item.outcome]}
        {item.overlap_rule_ids.length ? <p>Также распознают правила: {item.overlap_rule_ids.map(id => `#${id}`).join(', ')}</p> : null}
        {item.reason ? <p>{reasons[item.reason]}</p> : null}
      </li>)}
    </ul></details>}
  </section>
}
