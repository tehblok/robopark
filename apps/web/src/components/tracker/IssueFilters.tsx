import { type FormEvent, useState } from 'react'

export function IssueFilters({
  onApply,
  allowUntagged,
}: {
  onApply: (filters: { status?: string; robot?: string; untagged?: boolean }) => void
  allowUntagged: boolean
}) {
  const [status, setStatus] = useState('')
  const [robot, setRobot] = useState('')
  const [untagged, setUntagged] = useState(false)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    onApply({ status: status || undefined, robot: robot || undefined, untagged })
  }

  return (
    <form className="inline-form" onSubmit={submit}>
      <input onChange={(event) => setStatus(event.target.value)} placeholder="Status" value={status} />
      <input onChange={(event) => setRobot(event.target.value)} placeholder="Robot" value={robot} />
      {allowUntagged && (
        <label>
          <input checked={untagged} onChange={(event) => setUntagged(event.target.checked)} type="checkbox" />
          untagged
        </label>
      )}
      <button type="submit">Apply</button>
    </form>
  )
}
