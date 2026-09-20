export type NotificationEnableResult =
  | { status: 'unsupported' | 'denied' }
  | { status: 'granted'; endpoint: string; p256dh: string; auth: string }

function base64Url(buffer: ArrayBuffer | null): string {
  if (!buffer) return ''
  const bytes = new Uint8Array(buffer)
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/, '')
}
export async function enableSystemNotifications(
  registration: ServiceWorkerRegistration,
  applicationServerKey: Uint8Array<ArrayBuffer>,
): Promise<NotificationEnableResult> {
  if (typeof Notification === 'undefined' || !registration.pushManager) return { status: 'unsupported' }
  const permission = Notification.permission === 'default'
    ? await Notification.requestPermission()
    : Notification.permission
  if (permission !== 'granted') return { status: 'denied' }
  const subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey })
  return { status: 'granted', endpoint: subscription.endpoint, p256dh: base64Url(subscription.getKey('p256dh')), auth: base64Url(subscription.getKey('auth')) }
}
