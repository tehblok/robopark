// Campaign summaries are secondary to the live operational overview. Limit
// their concurrency across parks and discard reads from an abandoned screen.
let running = 0
const waiting: Array<() => void> = []

function abortError(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException('Request cancelled', 'AbortError')
}

export function limitOverviewCampaignRead<T>(read: (signal: AbortSignal) => Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    let started = false
    let settled = false
    const finish = (result: { value: T } | { error: unknown }) => {
      if (settled) return
      settled = true
      signal.removeEventListener('abort', onAbort)
      if (started) {
        running -= 1
        waiting.shift()?.()
      } else {
        const index = waiting.indexOf(start)
        if (index !== -1) waiting.splice(index, 1)
      }
      if ('error' in result) reject(result.error)
      else resolve(result.value)
    }
    const onAbort = () => finish({ error: abortError(signal) })
    const start = () => {
      if (signal.aborted) { onAbort(); return }
      started = true
      running += 1
      void Promise.resolve().then(() => {
        if (signal.aborted) throw abortError(signal)
        return read(signal)
      }).then(value => finish({ value }), error => finish({ error }))
    }
    if (signal.aborted) { reject(abortError(signal)); return }
    signal.addEventListener('abort', onAbort, { once: true })
    if (running < 2) start()
    else waiting.push(start)
  })
}
