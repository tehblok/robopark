export class StableMutationKey {
  private readonly entries = new Map<string, { payload: string; key: string }>()

  constructor(private readonly createKey: () => string = () => crypto.randomUUID()) {}

  get(action: string, payload: string): string {
    const current = this.entries.get(action)
    if (current?.payload === payload) return current.key
    const next = { payload, key: this.createKey() }
    this.entries.set(action, next)
    return next.key
  }

  succeeded(action: string, payload: string): void {
    if (this.entries.get(action)?.payload === payload) this.entries.delete(action)
  }

  cancel(action: string): void {
    this.entries.delete(action)
  }
}
