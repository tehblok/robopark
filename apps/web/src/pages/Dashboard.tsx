import { useCallback, useEffect, useRef, useState } from 'react'
import {
  api,
  type DashboardHistory,
  type DashboardHistoryPoint,
  type DashboardSummary,
} from '../api'
import { Alert, EmptyState, Panel } from '../components/PageShell'
import { mapApiError } from '../i18n/errors'
import { ru } from '../i18n/ru'
import { useParkContext } from '../park-context'

type DaySeriesPoint = {
  key: string
  label: string
  arrived: number
  departed: number
}

const CHART_WIDTH = 640
const CHART_HEIGHT = 220
const CHART_PAD = { top: 16, right: 16, bottom: 36, left: 40 }

function dayKeyMoscow(date: Date): string {
  return date.toLocaleDateString('en-CA', { timeZone: 'Europe/Moscow' })
}

function dayLabelMoscow(date: Date): string {
  return date.toLocaleDateString('ru-RU', {
    day: 'numeric',
    month: 'short',
    timeZone: 'Europe/Moscow',
  })
}

function lastSevenDays(): Date[] {
  const days: Date[] = []
  const now = new Date()
  for (let offset = 6; offset >= 0; offset -= 1) {
    const day = new Date(now)
    day.setDate(day.getDate() - offset)
    days.push(day)
  }
  return days
}

function aggregateHistoryByDay(points: DashboardHistoryPoint[]): DaySeriesPoint[] {
  const totals = new Map<string, { arrived: number; departed: number }>()

  for (const point of points) {
    const key = dayKeyMoscow(new Date(point.bucket_start))
    const current = totals.get(key) ?? { arrived: 0, departed: 0 }
    current.arrived += point.arrived_count
    current.departed += point.departed_count
    totals.set(key, current)
  }

  return lastSevenDays().map((day) => {
    const key = dayKeyMoscow(day)
    const bucket = totals.get(key) ?? { arrived: 0, departed: 0 }
    return {
      key,
      label: dayLabelMoscow(day),
      arrived: bucket.arrived,
      departed: bucket.departed,
    }
  })
}

function buildPolyline(
  values: number[],
  count: number,
  maxValue: number,
  plotWidth: number,
  plotHeight: number,
): string {
  if (count <= 1 || maxValue <= 0) {
    const y = CHART_PAD.top + plotHeight
    return values.map((_, index) => {
      const x = CHART_PAD.left + (index / Math.max(count - 1, 1)) * plotWidth
      return `${x},${y}`
    }).join(' ')
  }

  return values
    .map((value, index) => {
      const x = CHART_PAD.left + (index / (count - 1)) * plotWidth
      const y = CHART_PAD.top + plotHeight - (value / maxValue) * plotHeight
      return `${x},${y}`
    })
    .join(' ')
}

function BlockerHistoryChart({ points }: { points: DashboardHistoryPoint[] }) {
  const series = aggregateHistoryByDay(points)
  const plotWidth = CHART_WIDTH - CHART_PAD.left - CHART_PAD.right
  const plotHeight = CHART_HEIGHT - CHART_PAD.top - CHART_PAD.bottom
  const maxValue = Math.max(
    1,
    ...series.flatMap((day) => [day.arrived, day.departed]),
  )
  const arrivedLine = buildPolyline(
    series.map((day) => day.arrived),
    series.length,
    maxValue,
    plotWidth,
    plotHeight,
  )
  const departedLine = buildPolyline(
    series.map((day) => day.departed),
    series.length,
    maxValue,
    plotWidth,
    plotHeight,
  )
  const hasData = series.some((day) => day.arrived > 0 || day.departed > 0)

  if (!hasData) {
    return (
      <EmptyState>
        История blocker-ов за последние 7 дней пока не накоплена. Нажмите «Обновить» позже.
      </EmptyState>
    )
  }

  return (
    <div className="dashboard-chart-wrap">
      <svg
        aria-label="График пришедших и ушедших blocker-ов за 7 дней"
        className="dashboard-chart"
        role="img"
        viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
      >
        {[0, 0.5, 1].map((fraction) => {
          const y = CHART_PAD.top + plotHeight * (1 - fraction)
          const value = Math.round(maxValue * fraction)
          return (
            <g key={fraction}>
              <line
                className="dashboard-chart-grid"
                x1={CHART_PAD.left}
                x2={CHART_WIDTH - CHART_PAD.right}
                y1={y}
                y2={y}
              />
              <text className="dashboard-chart-axis" x={4} y={y + 4}>
                {value}
              </text>
            </g>
          )
        })}
        <polyline className="dashboard-chart-line dashboard-chart-line-arrived" points={arrivedLine} />
        <polyline className="dashboard-chart-line dashboard-chart-line-departed" points={departedLine} />
        {series.map((day, index) => {
          const x = CHART_PAD.left + (index / Math.max(series.length - 1, 1)) * plotWidth
          return (
            <text
              className="dashboard-chart-axis dashboard-chart-axis-x"
              key={day.key}
              textAnchor="middle"
              x={x}
              y={CHART_HEIGHT - 8}
            >
              {day.label}
            </text>
          )
        })}
      </svg>
      <div className="dashboard-chart-legend">
        <span className="dashboard-legend-item dashboard-legend-arrived">Пришли</span>
        <span className="dashboard-legend-item dashboard-legend-departed">Ушли</span>
      </div>
    </div>
  )
}

export function Dashboard() {
  const { parkId, parksLoading } = useParkContext()
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [history, setHistory] = useState<DashboardHistory | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const requestIdRef = useRef(0)

  const load = useCallback(async () => {
    if (parkId == null) return

    const requestId = ++requestIdRef.current
    setLoading(true)
    setError('')
    try {
      const [summaryData, historyData] = await Promise.all([
        api.dashboardSummary(parkId),
        api.dashboardHistory(parkId, 7),
      ])
      if (requestId !== requestIdRef.current) return
      setSummary(summaryData)
      setHistory(historyData)
    } catch (loadError) {
      if (requestId !== requestIdRef.current) return
      setSummary(null)
      setHistory(null)
      setError(mapApiError(loadError, ru.errors.load))
    } finally {
      if (requestId !== requestIdRef.current) return
      setLoading(false)
    }
  }, [parkId])

  useEffect(() => {
    if (parksLoading || parkId == null) {
      setSummary(null)
      setHistory(null)
      setError('')
      return
    }
    void load()
  }, [load, parkId, parksLoading])

  return (
    <div className="dashboard-page">
      <div className="dashboard-toolbar">
        <h1 className="dashboard-title">{ru.nav.dashboard}</h1>
        <button
          disabled={loading || parkId == null || parksLoading}
          onClick={() => void load()}
          type="button"
        >
          Обновить
        </button>
      </div>

      {error && <Alert tone="error">{error}</Alert>}

      {parkId == null && !parksLoading && (
        <Panel title={ru.nav.dashboard}>
          <EmptyState>Выберите парк в верхней панели, чтобы загрузить дашборд.</EmptyState>
        </Panel>
      )}

      {parkId != null && (
        <div className="dashboard-grid">
          <div className="dashboard-panel-chart">
            <Panel
              hint="Сумма по 2-часовым снимкам за последние 7 дней (Europe/Moscow)."
              title="Пришли и ушли за 7 дней"
            >
              {loading && <EmptyState>{ru.loading}</EmptyState>}
              {!loading && history && <BlockerHistoryChart points={history.points} />}
              {!loading && !history && !error && (
                <EmptyState>Нажмите «Обновить», чтобы загрузить историю.</EmptyState>
              )}
            </Panel>
          </div>

          <div className="dashboard-panel-kpi">
            <Panel title="Сегодня">
              {loading && <EmptyState>{ru.loading}</EmptyState>}
              {!loading && summary && (
                <div className="dashboard-kpi-grid">
                  <div className="dashboard-kpi">
                    <span className="dashboard-kpi-label">Пришли</span>
                    <span className="dashboard-kpi-value">{summary.arrived}</span>
                  </div>
                  <div className="dashboard-kpi">
                    <span className="dashboard-kpi-label">Ушли</span>
                    <span className="dashboard-kpi-value">{summary.done}</span>
                  </div>
                  <div className="dashboard-kpi">
                    <span className="dashboard-kpi-label">В очереди</span>
                    <span className="dashboard-kpi-value">{summary.queued}</span>
                  </div>
                </div>
              )}
              {!loading && !summary && !error && (
                <EmptyState>Нажмите «Обновить», чтобы загрузить показатели.</EmptyState>
              )}
            </Panel>
          </div>

          <div className="dashboard-panel-moving">
            <Panel title="Перемещение">
              {loading && <EmptyState>{ru.loading}</EmptyState>}
              {!loading && summary && summary.moving.length > 0 && (
                <ul className="card-list">
                  {summary.moving.map((item) => (
                    <li className="card" key={item.key}>
                      <div className="card-title">{item.key}</div>
                      <p>{item.summary}</p>
                    </li>
                  ))}
                </ul>
              )}
              {!loading && summary && summary.moving.length === 0 && !error && (
                <EmptyState>Нет blocker-ов в статусе «Перемещение».</EmptyState>
              )}
              {!loading && !summary && !error && (
                <EmptyState>Нажмите «Обновить», чтобы загрузить список.</EmptyState>
              )}
            </Panel>
          </div>
        </div>
      )}
    </div>
  )
}
