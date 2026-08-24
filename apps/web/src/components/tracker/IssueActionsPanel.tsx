import { type FormEvent, useState } from 'react'
import type { TrackerTransition } from '../../api'

export function IssueActionsPanel({
  canWrite,
  transitions,
  onComment,
  onAssign,
  onUnassign,
  onTransition,
  onClose,
}: {
  canWrite: boolean
  transitions: TrackerTransition[]
  onComment: (text: string) => Promise<void>
  onAssign: (assignee: string) => Promise<void>
  onUnassign: () => Promise<void>
  onTransition: (transition: string) => Promise<void>
  onClose: () => Promise<void>
}) {
  const [comment, setComment] = useState('')
  const [assignee, setAssignee] = useState('')

  const submitComment = async (event: FormEvent) => {
    event.preventDefault()
    if (!comment.trim()) return
    await onComment(comment.trim())
    setComment('')
  }

  if (!canWrite) return <p>Actions disabled by policy.</p>

  return (
    <section className="tracker-panel">
      <form className="inline-form" onSubmit={submitComment}>
        <input onChange={(event) => setComment(event.target.value)} placeholder="Comment" value={comment} />
        <button type="submit">Comment</button>
      </form>
      <form className="inline-form" onSubmit={(event) => { event.preventDefault(); if (assignee.trim()) { void onAssign(assignee.trim()); setAssignee('') } }}>
        <input onChange={(event) => setAssignee(event.target.value)} placeholder="Assignee" value={assignee} />
        <button type="submit">Assign</button>
      </form>
      <div className="actions">
        <button onClick={() => void onUnassign()} type="button">Unassign</button>
        {transitions.map((item) => (
          <button key={item.id} onClick={() => void onTransition(item.id)} type="button">{item.display}</button>
        ))}
        <button onClick={() => void onClose()} type="button">Close</button>
      </div>
    </section>
  )
}
