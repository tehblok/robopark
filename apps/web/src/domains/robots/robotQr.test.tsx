import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { RobotQrButton } from './RobotQrButton'
import { normalizeQrVin, robotLabelHtml } from './robotQr'

afterEach(() => vi.restoreAllMocks())

it.each(['1441', 'A1441', ' yasadr00000001441 '])('normalizes %s to the same printable VIN', raw => {
  expect(normalizeQrVin(raw)).toBe('YASADR00000001441')
})

it.each(['', 'YASADR1441', '123456789012', 'https://evil.test/1441', '<script>alert(1)</script>'])('rejects ambiguous or non-robot content %s', raw => {
  expect(normalizeQrVin(raw)).toBeNull()
  expect(() => robotLabelHtml(raw, { paper: 'a4', width: 50, height: 50, copies: 1 })).toThrow()
})

it('rejects invalid print dimensions and unbounded copy counts', () => {
  for (const options of [{ width: 20 }, { height: NaN }, { copies: 101 }, { copies: 1.5 }]) {
    expect(() => robotLabelHtml('1441', { paper: 'a4', width: 50, height: 50, copies: 1, ...options })).toThrow()
  }
})

it('prints the requested number of labels with the full readable VIN and no application UI', () => {
  const doc = new DOMParser().parseFromString(robotLabelHtml('1441', { paper: 'a4', width: 50, height: 50, copies: 3 }), 'text/html')
  expect(doc.querySelectorAll('main > section')).toHaveLength(3)
  expect([...doc.querySelectorAll('section p')].map(p => p.textContent)).toEqual(Array(3).fill('YASADR00000001441'))
  expect(doc.querySelectorAll('svg')).toHaveLength(3)
  expect(doc.querySelector('script, iframe, link, nav, button')).toBeNull()
})

it('removes the previous valid preview and disables printing when the input becomes invalid', () => {
  render(<RobotQrButton initialValue="1441" />)
  fireEvent.click(screen.getByRole('button', { name: 'QR и печать' }))
  expect(screen.getByRole('img', { name: 'QR: YASADR00000001441' })).toBeVisible()
  fireEvent.change(screen.getByLabelText('Номер или VIN для QR'), { target: { value: 'YASADR1441' } })
  expect(screen.queryByRole('img')).toBeNull()
  expect(screen.queryByRole('link', { name: 'Скачать QR (SVG)' })).toBeNull()
  expect(screen.getByRole('button', { name: 'Печатать' })).toBeDisabled()
})

it('keeps the VIN and preview available when the print popup is blocked', () => {
  vi.spyOn(window, 'open').mockReturnValue(null)
  render(<RobotQrButton vin="YASADR00000001441" />)
  fireEvent.click(screen.getByRole('button', { name: 'QR и печать' }))
  fireEvent.click(screen.getByRole('button', { name: 'Печатать' }))
  expect(screen.getByRole('alert')).toHaveTextContent('Разрешите всплывающее окно')
  expect(screen.getByLabelText('Номер или VIN для QR')).toHaveValue('YASADR00000001441')
  expect(screen.getByRole('img', { name: 'QR: YASADR00000001441' })).toBeVisible()
})
