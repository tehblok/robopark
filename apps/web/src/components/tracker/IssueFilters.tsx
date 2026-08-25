import { type FormEvent, useState } from 'react'
import { ru } from '../../i18n/ru'

export type IssueFilterValues = {
  queue?: string
  park?: string
  status?: string
  robot?: string
  assignee?: string
  untagged?: boolean
  age_hours?: number
}

type Preset = 'all' | 'mine' | 'unassigned' | 'old'

export function IssueFilters({
  onApply,
  allowUntagged,
  defaultQueue = 'SDCFLEETOPS',
  defaultPark = '',
  trackerLogin,
  loading,
}: {
  onApply: (filters: IssueFilterValues) => void
  allowUntagged: boolean
  defaultQueue?: string
  defaultPark?: string
  /** Startrek login of the current user — required for the «Мои» preset. */
  trackerLogin?: string | null
  loading?: boolean
}) {
  const [queue, setQueue] = useState(defaultQueue)
  const [park, setPark] = useState(defaultPark)
  const [status, setStatus] = useState('')
  const [robot, setRobot] = useState('')
  const [assignee, setAssignee] = useState<string | undefined>()
  const [untagged, setUntagged] = useState(false)
  const [ageHours, setAgeHours] = useState('')
  const [preset, setPreset] = useState<Preset>('all')
  const [expanded, setExpanded] = useState(false)
  const [presetHint, setPresetHint] = useState('')

  const build = (overrides: Partial<IssueFilterValues> = {}): IssueFilterValues => {
    const age = Number(ageHours)
    return {
      queue: queue.trim() || undefined,
      park: untagged ? undefined : park.trim() || undefined,
      status: status.trim() || undefined,
      robot: robot.trim() || undefined,
      assignee,
      untagged: untagged || undefined,
      age_hours: Number.isFinite(age) && age > 0 ? age : undefined,
      ...overrides,
    }
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    onApply(build())
  }

  const applyPreset = (next: Preset) => {
    setPreset(next)
    setPresetHint('')

    if (next === 'mine') {
      const login = trackerLogin?.trim()
      if (!login) {
        setPresetHint('Укажите Startrek-логин в админке, чтобы фильтровать «Мои».')
        return
      }
      setAssignee(login)
      setAgeHours('')
      onApply(build({ assignee: login, age_hours: undefined }))
      return
    }

    if (next === 'unassigned') {
      setAssignee('empty')
      setAgeHours('')
      onApply(build({ assignee: 'empty', age_hours: undefined }))
      return
    }

    if (next === 'old') {
      setAssignee(undefined)
      setAgeHours('24')
      onApply(build({ assignee: undefined, age_hours: 24 }))
      return
    }

    setAssignee(undefined)
    setAgeHours('')
    onApply(build({ assignee: undefined, age_hours: undefined }))
  }

  const reset = () => {
    setQueue(defaultQueue)
    setPark(defaultPark)
    setStatus('')
    setRobot('')
    setAssignee(undefined)
    setUntagged(false)
    setAgeHours('')
    setPreset('all')
    setPresetHint('')
    onApply({
      queue: defaultQueue.trim() || undefined,
      park: defaultPark.trim() || undefined,
    })
  }

  return (
    <div className="issue-filters">
      <div className="issue-presets">
        <button
          className={`btn btn-filter${preset === 'all' ? ' is-active' : ''}`}
          onClick={() => applyPreset('all')}
          type="button"
        >
          {ru.tracker.filters.presetAll}
        </button>
        <button
          className={`btn btn-filter${preset === 'mine' ? ' is-active' : ''}`}
          disabled={!trackerLogin?.trim()}
          onClick={() => applyPreset('mine')}
          title={trackerLogin?.trim() ? undefined : 'Startrek-логин не задан'}
          type="button"
        >
          {ru.tracker.filters.presetMine}
        </button>
        <button
          className={`btn btn-filter${preset === 'unassigned' ? ' is-active' : ''}`}
          onClick={() => applyPreset('unassigned')}
          type="button"
        >
          {ru.tracker.filters.presetUnassigned}
        </button>
        <button
          className={`btn btn-filter${preset === 'old' ? ' is-active' : ''}`}
          onClick={() => applyPreset('old')}
          type="button"
        >
          {ru.tracker.filters.presetOld}
        </button>
        <button
          aria-expanded={expanded}
          className="btn btn-ghost issue-filters-toggle"
          onClick={() => setExpanded((value) => !value)}
          type="button"
        >
          {ru.tracker.filters.title}
        </button>
      </div>

      {presetHint && <p className="field-hint">{presetHint}</p>}

      <form className={`issue-filter-form${expanded ? ' is-open' : ''}`} onSubmit={submit}>
        <label className="issue-filter-field">
          <span>{ru.tracker.filters.queue}</span>
          <input
            onChange={(event) => setQueue(event.target.value)}
            placeholder="SDCFLEETOPS"
            value={queue}
          />
        </label>

        <label className="issue-filter-field">
          <span>{ru.tracker.filters.park}</span>
          <input
            disabled={untagged}
            onChange={(event) => setPark(event.target.value)}
            value={park}
          />
        </label>

        <label className="issue-filter-field">
          <span>{ru.tracker.filters.status}</span>
          <input onChange={(event) => setStatus(event.target.value)} value={status} />
        </label>

        <label className="issue-filter-field">
          <span>{ru.tracker.filters.robot}</span>
          <input
            onChange={(event) => setRobot(event.target.value)}
            placeholder="447"
            value={robot}
          />
        </label>

        <label className="issue-filter-field">
          <span>{ru.tracker.filters.age}</span>
          <input
            min="1"
            onChange={(event) => setAgeHours(event.target.value)}
            type="number"
            value={ageHours}
          />
        </label>

        {allowUntagged && (
          <label className="issue-filter-check">
            <input
              checked={untagged}
              onChange={(event) => setUntagged(event.target.checked)}
              type="checkbox"
            />
            <span>{ru.tracker.filters.untagged}</span>
          </label>
        )}

        <div className="issue-filter-actions">
          <button className="btn" disabled={loading} type="submit">
            {loading ? ru.loading : ru.tracker.filters.apply}
          </button>
          <button className="btn btn-ghost" onClick={reset} type="button">
            {ru.tracker.filters.reset}
          </button>
        </div>
      </form>
    </div>
  )
}
