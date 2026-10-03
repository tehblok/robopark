import qrcode from 'qrcode-generator'

export function totpUri(username: string, secret: string): string {
  if (!/^[A-Z2-7]{16,128}$/.test(secret)) throw new Error('invalid_totp_secret')
  const label = encodeURIComponent(`Robopark:${username}`)
  return `otpauth://totp/${label}?secret=${encodeURIComponent(secret)}&issuer=Robopark&digits=6&period=30`
}

export function totpQrSource(username: string, secret: string): string {
  const code = qrcode(0, 'M')
  code.addData(totpUri(username, secret), 'Byte')
  code.make()
  const svg = code.createSvgTag({ cellSize: 4, margin: 4, scalable: true })
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`
}
