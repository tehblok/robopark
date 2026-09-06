import { useId, useState } from 'react'
import { Button } from '../../design-system/actions/Button'
import { Dialog } from '../../design-system/overlays/Dialog'
import { normalizeQrVin, printRobotLabel, robotQrSvg, validLabelOptions, type RobotLabelOptions } from './robotQr'
import './robot-qr.css'

export function RobotQrButton({ vin, initialValue = '' }: { vin?: string; initialValue?: string }) {
  const [open, setOpen] = useState(false)
  return <>
    <Button type="button" variant="secondary" leadingIcon="scan" onClick={() => setOpen(true)}>QR и печать</Button>
    {open ? <RobotQrDialog key={vin ?? initialValue} value={vin ?? initialValue} fixed={Boolean(vin)} onClose={() => setOpen(false)} /> : null}
  </>
}

function RobotQrDialog({ value, fixed, onClose }: { value: string; fixed: boolean; onClose: () => void }) {
  const [raw, setRaw] = useState(value)
  const [options, setOptions] = useState<RobotLabelOptions>({ paper: 'a4', width: 50, height: 50, copies: 1 })
  const [error, setError] = useState<string | null>(null)
  const id = useId()
  const vin = normalizeQrVin(raw)
  const svg = vin ? robotQrSvg(vin) : null
  const source = svg ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}` : undefined
  const valid = Boolean(vin && validLabelOptions(options))
  return <Dialog open onOpenChange={next => { if (!next) onClose() }} title="QR робота и печать"
    description="QR содержит VIN. Его можно прочитать сканером в Robopark."
    footer={<>
      <Button type="button" disabled={!valid} onClick={() => {
        setError(null)
        try { printRobotLabel(raw, options) } catch (reason) { setError(reason instanceof Error ? reason.message : 'Не удалось открыть печать.') }
      }}>Печатать</Button>
      {source && vin ? <a className="rp-button rp-button--secondary rp-button--comfortable" download={`${vin}.svg`} href={source}>Скачать QR (SVG)</a> : null}
    </>}>
    <div className="rp-qr-form">
      <label htmlFor={`${id}-vin`}>Номер или VIN для QR</label>
      <input id={`${id}-vin`} value={raw} readOnly={fixed} maxLength={32} placeholder="1441 или YASADR00000001441" onChange={event => { setRaw(event.target.value); setError(null) }} aria-invalid={Boolean(raw && !vin)} aria-describedby={`${id}-hint`} />
      <p id={`${id}-hint`} className="rp-qr-hint">{raw && !vin ? 'Введите номер до 11 цифр или полный VIN: YASADR и 11 цифр.' : 'Проверьте VIN под кодом перед печатью.'}</p>
      {source && vin ? <figure className="rp-qr-preview"><img src={source} alt={`QR: ${vin}`} width={232} height={232} /><figcaption>{vin}</figcaption></figure> : null}
      <label htmlFor={`${id}-paper`}>Бумага</label>
      <select id={`${id}-paper`} value={options.paper} onChange={event => setOptions({ ...options, paper: event.target.value as RobotLabelOptions['paper'] })}>
        <option value="a4">A4 — этикетки на листе</option><option value="label">Принтер этикеток — по одной на страницу</option>
      </select>
      <div className="rp-qr-dimensions">
        <label>Ширина, мм<input type="number" min={30} max={100} step={1} value={options.width || ''} onChange={event => setOptions({ ...options, width: Number(event.target.value) })} /></label>
        <label>Высота, мм<input type="number" min={30} max={150} step={1} value={options.height || ''} onChange={event => setOptions({ ...options, height: Number(event.target.value) })} /></label>
        <label>Копий<input type="number" min={1} max={100} step={1} value={options.copies || ''} onChange={event => setOptions({ ...options, copies: Number(event.target.value) })} /></label>
      </div>
      {!validLabelOptions(options) ? <p role="alert">Ширина: 30–100 мм, высота: 30–150 мм, копий: 1–100. Используйте целые числа.</p> : null}
      <p className="rp-qr-hint">В окне печати выберите масштаб 100% и отключите колонтитулы. Для принтера этикеток задайте такой же размер бумаги в драйвере.</p>
      {error ? <p role="alert">{error}</p> : null}
    </div>
  </Dialog>
}
