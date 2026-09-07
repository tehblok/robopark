import qrcode from 'qrcode-generator'

export type RobotLabelOptions = { paper: 'a4' | 'label'; width: number; height: number; copies: number }

export function normalizeQrVin(raw: string): string | null {
  const value = raw.trim().toUpperCase()
  if (/^YASADR\d{11}$/.test(value)) return value
  if (/^A?\d{1,11}$/.test(value)) return `YASADR${value.replace(/^A/, '').padStart(11, '0')}`
  return null
}

export function robotQrSvg(raw: string): string {
  const vin = normalizeQrVin(raw)
  if (!vin) throw new Error('Введите номер робота или VIN вида YASADR00000001441.')
  const code = qrcode(0, 'M')
  code.addData(vin, 'Alphanumeric')
  code.make()
  // Four modules of white quiet zone remain part of every exported/printed SVG.
  return code.createSvgTag({ cellSize: 1, margin: 4, scalable: true })
}

export function validLabelOptions(options: RobotLabelOptions): boolean {
  return ['a4', 'label'].includes(options.paper)
    && Number.isInteger(options.width) && options.width >= 30 && options.width <= 100
    && Number.isInteger(options.height) && options.height >= 30 && options.height <= 150
    && Number.isInteger(options.copies) && options.copies >= 1 && options.copies <= 100
}

export function robotLabelHtml(raw: string, options: RobotLabelOptions): string {
  const vin = normalizeQrVin(raw)
  if (!vin || !validLabelOptions(options)) throw new Error('Проверьте VIN, размер этикетки и количество копий.')
  const { paper, width, height, copies } = options
  const svg = robotQrSvg(vin)
  const qrSize = Math.min(width - 4, height - 10, 60)
  const label = `<section class="label" aria-label="Этикетка ${vin}">${svg}<p>${vin}</p></section>`
  // Interpolated data is exclusively a normalized VIN and bounded integers.
  return `<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>QR ${vin}</title>
<style>
@page { size: ${paper === 'a4' ? 'A4' : `${width}mm ${height}mm`}; margin: ${paper === 'a4' ? '10mm' : '0'}; }
* { box-sizing: border-box; } html, body { margin: 0; padding: 0; background: white; color: black; }
main { ${paper === 'a4' ? `display: grid; grid-template-columns: repeat(${Math.floor(190 / width)}, ${width}mm);` : 'display: block;'} }
.label { width: ${width}mm; height: ${height}mm; padding: 2mm; display: flex; flex-direction: column; align-items: center; justify-content: center; break-inside: avoid; page-break-inside: avoid; ${paper === 'label' ? 'break-after: page; page-break-after: always;' : ''} }
.label:last-child { break-after: auto; page-break-after: auto; }
svg { display: block; flex: none; width: ${qrSize}mm; height: ${qrSize}mm; shape-rendering: crispEdges; }
p { margin: 1mm 0 0; font: bold 8pt monospace; white-space: nowrap; text-align: center; }
@media screen { body { padding: 12px; } main { width: fit-content; } .label { outline: 1px solid #ccc; } }
</style></head><body><main>${label.repeat(copies)}</main></body></html>`
}

export function printRobotLabel(raw: string, options: RobotLabelOptions): void {
  const html = robotLabelHtml(raw, options)
  const popup = window.open('', '_blank', 'popup,width=800,height=700')
  if (!popup) throw new Error('Разрешите всплывающее окно для печати и повторите.')
  popup.opener = null
  popup.document.open()
  popup.document.write(html)
  popup.document.close()
  // The page has only inline SVG and system fonts; there are no remote assets.
  popup.focus()
  popup.print()
}
