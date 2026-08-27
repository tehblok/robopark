import {
  api,
  type DashboardHistoryPoint,
} from '../api'
import { Alert, Panel } from '../components/PageShell'
import { EmptyBlock, SkeletonKpi, SkeletonList, Spinner } from '../components/ui/Feedback'
import { mapApiError } from '../i18n/errors'
import { ru } from '../i18n/ru'
import { useCachedResource } from '../lib/resource'
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
      <EmptyBlock
        hint="Фоновый сбор идёт каждые 2 часа — данные появятся после первого прохода."
        icon="📊"
        title="История ещё не накоплена"
      />
    )
  }

  // Area under each line makes the chart readable at a glance on a phone.
  const areaFor = (line: string) =>
    `${CHART_PAD.left},${CHART_PAD.top + plotHeight} ${line} ${
      CHART_PAD.left + plotWidth
    },${CHART_PAD.top + plotHeight}`

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
        <polygon className="dashboard-chart-area dashboard-chart-area-arrived" points={areaFor(arrivedLine)} />
        <polygon className="dashboard-chart-area dashboard-chart-area-departed" points={areaFor(departedLine)} />
        <polyline className="dashboard-chart-line dashboard-chart-line-arrived" points={arrivedLine} />
        <polyline className="dashboard-chart-line dashboard-chart-line-departed" points={departedLine} />
        {series.map((day, index) => {
          const x = CHART_PAD.left + (index / Math.max(series.length - 1, 1)) * plotWidth
          const yArrived =
            CHART_PAD.top + plotHeight - (day.arrived / maxValue) * plotHeight
          const yDeparted =
            CHART_PAD.top + plotHeight - (day.departed / maxValue) * plotHeight
          return (
            <g key={`dots-${day.key}`}>
              <circle className="dashboard-chart-dot dashboard-chart-dot-arrived" cx={x} cy={yArrived} r={3} />
              <circle className="dashboard-chart-dot dashboard-chart-dot-departed" cx={x} cy={yDeparted} r={3} />
              <title>{`${day.label}: пришли ${day.arrived}, ушли ${day.departed}`}</title>
            </g>
          )
        })}
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

  const summaryRes = useCachedResource(
    parkId == null ? '' : `dashboard:summary:${parkId}`,
    () => api.dashboardSummary(parkId as number),
    { enabled: parkId != null && !parksLoading },
  )
  const historyRes = useCachedResource(
    parkId == null ? '' : `dashboard:history:${parkId}:7`,
    () => api.dashboardHistory(parkId as number, 7),
    { enabled: parkId != null && !parksLoading },
  )

  const summary = summaryRes.data ?? null
  const history = historyRes.data ?? null
  const summaryError = summaryRes.error
    ? mapApiError(summaryRes.error, ru.errors.load)
    : ''
  const historyError = historyRes.error
    ? mapApiError(historyRes.error, ru.errors.load)
    : ''
  const loading = summaryRes.isRevalidating || historyRes.isRevalidating
  const showSummarySkeleton = summaryRes.isLoading && !summary && !summaryError
  const showHistorySkeleton = historyRes.isLoading && !history && !historyError

  const refresh = () => {
    void summaryRes.refresh()
    void historyRes.refresh()
  }

  return (
    <div className="dashboard-page animate-in">
      <div className="dashboard-toolbar">
        <h1 className="dashboard-title">{ru.nav.dashboard}</h1>
        <button
          className="btn btn-secondary"
          disabled={loading || parkId == null || parksLoading}
          onClick={refresh}
          type="button"
        >
          {loading ? <Spinner label="Обновление" /> : 'Обновить'}
        </button>
      </div>

      {parkId == null && !parksLoading && (
        <EmptyBlock
          hint="Дашборд показывает данные выбранного парка."
          icon="🏭"
          title="Выберите парк в верхней панели"
        />
      )}

      {parkId != null && (
        <div className="dashboard-grid">
          <div className="dashboard-panel-chart">
            <Panel
              hint="Сумма по 2-часовым снимкам за последние 7 дней (Europe/Moscow)."
              title="Пришли и ушли за 7 дней"
            >
              {historyError && <Alert tone="error">{historyError}</Alert>}
              {showHistorySkeleton && <SkeletonList rows={1} />}
              {history && <BlockerHistoryChart points={history.points} />}
              {!history && !historyError && !showHistorySkeleton && (
                <EmptyBlock icon="📈" title="Нажмите «Обновить», чтобы загрузить историю" />
              )}
            </Panel>
          </div>

          <div className="dashboard-panel-kpi">
            <Panel title="Сегодня">
              {summaryError && <Alert tone="error">{summaryError}</Alert>}
              {showSummarySkeleton && <SkeletonKpi />}
              {summary && (
                <div className="dashboard-kpi-grid">
                  <div className="dashboard-kpi tone-arrived">
                    <span className="dashboard-kpi-label">Пришли</span>
                    <span className="dashboard-kpi-value">{summary.arrived}</span>
                  </div>
                  <div className="dashboard-kpi tone-done">
                    <span className="dashboard-kpi-label">Ушли</span>
                    <span className="dashboard-kpi-value">{summary.done}</span>
                  </div>
                  <div className="dashboard-kpi tone-queued">
                    <span className="dashboard-kpi-label">В очереди</span>
                    <span className="dashboard-kpi-value">{summary.queued}</span>
                  </div>
                </div>
              )}
              {!summary && !summaryError && !showSummarySkeleton && (
                <EmptyBlock icon="📋" title="Нажмите «Обновить», чтобы загрузить показатели" />
              )}
            </Panel>
          </div>

          <div className="dashboard-panel-moving">
            <Panel title="Перемещение">
              {summaryError && <Alert tone="error">{summaryError}</Alert>}
              {showSummarySkeleton && <SkeletonList rows={2} />}
              {summary && summary.moving.length > 0 && (
                <ul className="card-list">
                  {summary.moving.map((item) => (
                    <li className="card" key={item.key}>
                      <div className="card-title">{item.key}</div>
                      <p>{item.summary}</p>
                    </li>
                  ))}
                </ul>
              )}
              {summary && summary.moving.length === 0 && !summaryError && (
                <EmptyBlock icon="✅" title="Нет блокеров в статусе «Перемещение»" />
              )}
              {!summary && !summaryError && !showSummarySkeleton && (
                <EmptyBlock icon="🚚" title="Нажмите «Обновить», чтобы загрузить список" />
              )}
            </Panel>
          </div>
        </div>
      )}
    </div>
  )
}
