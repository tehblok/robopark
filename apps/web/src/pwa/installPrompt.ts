export type InstallPromptEvent = Event & {
  prompt: () => Promise<void>
  userChoice: Promise<{ outcome: string }>
}

let pending: InstallPromptEvent | null = null
const listeners = new Set<(event: InstallPromptEvent | null) => void>()

function publish(event: InstallPromptEvent | null) {
  pending = event
  for (const listener of listeners) listener(event)
}

// This module loads before the auth shell, so a prompt raised on /login survives sign-in.
if (typeof window !== 'undefined') {
  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault()
    publish(event as InstallPromptEvent)
  })
  window.addEventListener('appinstalled', () => publish(null))
}

export function currentInstallPrompt() {
  return pending
}

export function subscribeInstallPrompt(listener: (event: InstallPromptEvent | null) => void) {
  listeners.add(listener)
  listener(pending)
  return () => { listeners.delete(listener) }
}

export function clearInstallPrompt() {
  publish(null)
}
