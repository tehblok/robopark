import { useId } from 'react'
import type { OperationsFlow } from '../../api'
import { Panel } from '../../design-system/layout/PageLayout'
import { flowBuckets, moscowDate } from './operations'

export function FlowChart({ flow }: { flow: OperationsFlow }) {
  const id = useId()
  const rows = flowBuckets(flow)
  const max = Math.max(1, ...flow.points.flatMap(point => [point.arrived_count, point.departed_count]))
  const x = (index: number) => 40 + index * 630 / Math.max(1, rows.length - 1)
  const y = (value: number) => 180 - value * 145 / max
  const groups: number[][] = []
  rows.forEach((row, index) => {
    if (!row.point) return
    if (index === 0 || !rows[index - 1].point) groups.push([])
    groups[groups.length - 1].push(index)
  })
  return <Panel title="Поток задач: пришло / ушло" description="Пришло — созданные задачи. Ушло — задачи с решением fixed по дате решения. Это не физические перемещения роботов.">
    <p className="rp-insights-note">История за выбранный период · время Москвы (Europe/Moscow). Покрытие: {flow.observed_buckets} из {flow.expected_buckets} интервалов по 2 часа.{!flow.complete ? ' Пробелы не считаются нулями.' : ''}{flow.legacy_buckets > 0 ? ` Старых интервалов: ${flow.legacy_buckets}; в новый расчёт не включены.` : ''}</p>
    {flow.points.length === 0 ? <p>История пока не накоплена</p> : <>
      <div className="rp-flow-legend"><span>● Пришло</span><span>◆ Ушло</span></div>
      <svg className="rp-flow-chart" viewBox="0 0 710 230" role="img" aria-labelledby={`${id}-title ${id}-desc`}>
        <title id={`${id}-title`}>Поток задач за выбранный период</title><desc id={`${id}-desc`}>Две серии: пришло и ушло. Разрывы — интервалы без наблюдений. Точные значения приведены в таблице.</desc>
        <line x1="40" x2="670" y1="180" y2="180" stroke="var(--rp-border)" />
        <text x="5" y="40">{max}</text><text x="15" y="185">0</text>
        {(['arrivals', 'departures'] as const).map(series => <g key={series} className={`rp-flow-${series}`}>
          {groups.map((group, index) => <polyline key={index} fill="none" stroke="currentColor" strokeWidth="2" strokeDasharray={series === 'departures' ? '5 3' : undefined} points={group.map(i => `${x(i)},${y(series === 'arrivals' ? rows[i].point!.arrived_count : rows[i].point!.departed_count)}`).join(' ')} />)}
          {rows.map((row, index) => row.point ? <circle key={row.time} cx={x(index)} cy={y(series === 'arrivals' ? row.point.arrived_count : row.point.departed_count)} r={series === 'arrivals' ? 3 : 2} fill="currentColor" data-series={series} data-value={series === 'arrivals' ? row.point.arrived_count : row.point.departed_count}><title>{moscowDate(row.time)} · {series === 'arrivals' ? 'Пришло' : 'Ушло'}: {series === 'arrivals' ? row.point.arrived_count : row.point.departed_count}</title></circle> : null)}
        </g>)}
        {rows.length > 0 ? <><text x="40" y="215">{moscowDate(rows[0].time)}</text><text x="670" y="215" textAnchor="end">{moscowDate(rows[rows.length - 1].time)}</text></> : null}
      </svg>
      <details><summary>Таблица значений</summary><div className="rp-insights-table-scroll" role="region" aria-label="Таблица потока" tabIndex={0}><table aria-label="Поток задач · время Москвы"><thead><tr><th scope="col">Начало интервала (МСК)</th><th scope="col">Пришло</th><th scope="col">Ушло</th></tr></thead><tbody>{rows.map(row => <tr key={row.time}><th scope="row">{moscowDate(row.time)}</th><td>{row.point?.arrived_count ?? 'Нет данных'}</td><td>{row.point?.departed_count ?? 'Нет данных'}</td></tr>)}</tbody></table></div></details>
    </>}
  </Panel>
}
