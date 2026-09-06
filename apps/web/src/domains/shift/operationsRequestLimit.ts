// Keep the budget across scope changes while retired in-flight calls finish.
let active = 0
const waiting: (() => void)[] = []

export function limitOperationsRequest<T>(request: () => Promise<T>): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const start = () => {
      active += 1
      void request().then(resolve, reject).finally(() => {
        active -= 1
        waiting.shift()?.()
      })
    }
    if (active < 3) start()
    else waiting.push(start)
  })
}
