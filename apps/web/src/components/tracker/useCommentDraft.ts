import { useEffect, useRef, useState } from 'react'

type Draft = { text: string; revision: string }
const changedEvent = 'robopark:comment-draft'

export function commentDraftKey(owner?: string, issueKey?: string) {
  return owner && issueKey
    ? `robopark:comment-draft:v1:${encodeURIComponent(owner)}:${encodeURIComponent(issueKey)}`
    : null
}

function parseDraft(value: string | null): Draft | null {
  try {
    const parsed: unknown = value ? JSON.parse(value) : null
    return parsed && typeof parsed === 'object' && 'text' in parsed && 'revision' in parsed
      && typeof parsed.text === 'string' && typeof parsed.revision === 'string'
      ? { text: parsed.text, revision: parsed.revision } : null
  } catch { return null }
}

function readDraft(key: string | null): Draft | null {
  try { return key ? parseDraft(localStorage.getItem(key)) : null } catch { return null }
}

// Called from a composer keyed by account and issue. Writes happen only on edits,
// never during effect cleanup, so a late unmount cannot restore a sent draft.
export function useCommentDraft(owner?: string, issueKey?: string) {
  const key = commentDraftKey(owner, issueKey)
  const [draft, setDraft] = useState<Draft | null>(() => readDraft(key))
  const current = useRef(draft)
  const accept = (next: Draft | null) => {
    current.current = next
    setDraft(next)
  }

  useEffect(() => {
    if (!key) return
    const onStorage = (event: StorageEvent) => {
      if (event.key === key || event.key === null) accept(readDraft(key))
    }
    const onLocalChange = (event: Event) => {
      const change = (event as CustomEvent<{ key: string; draft: Draft | null }>).detail
      if (change.key === key) accept(change.draft)
    }
    window.addEventListener('storage', onStorage)
    window.addEventListener(changedEvent, onLocalChange)
    return () => {
      window.removeEventListener('storage', onStorage)
      window.removeEventListener(changedEvent, onLocalChange)
    }
  }, [key])

  const save = (next: Draft | null) => {
    if (key) {
      try {
        if (next) localStorage.setItem(key, JSON.stringify(next))
        else localStorage.removeItem(key)
      } catch { /* The composer remains usable when storage is unavailable/full. */ }
      window.dispatchEvent(new CustomEvent(changedEvent, { detail: { key, draft: next } }))
    }
    accept(next)
  }

  return {
    comment: draft?.text ?? '',
    setComment: (text: string) => save(text ? { text, revision: crypto.randomUUID() } : null),
    // Capture a revision before sending, preserving edits made while it is in flight,
    // including those made in another tab or after navigating away and back.
    capture: () => {
      const submitted = current.current
      const storedAtSubmission = readDraft(key)
      return () => {
        const storedNow = readDraft(key)
        const latest = storedNow?.revision === storedAtSubmission?.revision ? current.current : storedNow
        if (submitted && latest?.revision === submitted.revision) save(null)
      }
    },
  }
}
