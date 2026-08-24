import { type FormEvent, useState } from 'react'

export type IssueFilterValues = {
  queue?: string
  park?: string
  status?: string
  robot?: string
  untagged?: boolean
}

export function IssueFilters({
  onApply,
  allowUntagged,
  defaultQueue = 'SDCFLEETOPS',
  defaultPark = '',
}: {
  onApply: (filters: IssueFilterValues) => void
  allowUntagged: boolean
  defaultQueue?: string
  defaultPark?: string
}) {
  const [queue, setQueue] = useState(defaultQueue)
  const [park, setPark] = useState(defaultPark)
  const [status, setStatus] = useState('')
  const [robot, setRobot] = useState('')
  const [untagged, setUntagged] = useState(false)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    onApply({
      queue: queue.trim() || undefined,
      park: untagged ? undefined : park.trim() || undefined,
      status: status.trim() || undefined,
      robot: robot.trim() || undefined,
      untagged: untagged || undefined,
    })
  }

  return (
    <form className="inline-form" onSubmit={submit}>
      <input
        aria-label="Очередь"
        onChange={(event) => setQueue(event.target.value)}
        placeholder="Очередь (SDCFLEETOPS)"
        value={queue}
      />
      <input
        aria-label="Тег парка"
        disabled={untagged}
        onChange={(event) => setPark(event.target.value)}
        placeholder="Тег парка"
        value={park}
      />
      <input
        aria-label="Статус"
        onChange={(event) => setStatus(event.target.value)}
        placeholder="Статус"
        value={status}
      />
      <input
        aria-label="Робот"
        onChange={(event) => setRobot(event.target.value)}
        placeholder="Робот"
        value={robot}
      />
      {allowUntagged && (
        <label>
          <input
            checked={untagged}
            onChange={(event) => setUntagged(event.target.checked)}
            type="checkbox"
          />
          Неразмеченные
        </label>
      )}
      <button type="submit">Применить</button>
    </form>
  )
}
