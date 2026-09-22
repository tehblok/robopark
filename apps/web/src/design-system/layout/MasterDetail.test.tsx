/// <reference types="node" />

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { MasterDetail } from './MasterDetail'

describe('MasterDetail', () => {
  it('keeps list and detail panes in the desktop two-column layout', () => {
    const { container } = render(
      <MasterDetail detail={<p>Детали заявки</p>} detailOpen={false} list={<p>Список заявок</p>} onBack={vi.fn()} />,
    )

    expect(container.querySelector('.rp-master-detail')).toHaveAttribute('data-detail-open', 'false')
    expect(screen.getByRole('region', { name: 'Список' })).toHaveTextContent('Список заявок')
    expect(screen.getByRole('region', { name: 'Детали' })).toHaveTextContent('Детали заявки')
  })

  it('offers a focusable mobile back button that invokes onBack', () => {
    const onBack = vi.fn()

    render(
      <MasterDetail detail={<p>Детали заявки</p>} detailOpen list={<p>Список заявок</p>} onBack={onBack} />,
    )

    const backButton = screen.getByRole('button', { name: 'Назад к списку' })
    expect(screen.getByRole('region', { name: 'Список' })).toHaveTextContent('Список заявок')
    expect(screen.getByRole('region', { name: 'Детали' })).toHaveTextContent('Детали заявки')
    backButton.focus()
    expect(backButton).toHaveFocus()
    fireEvent.click(backButton)
    expect(onBack).toHaveBeenCalledOnce()
  })

  it('does not reserve a desktop detail column when no detail exists', () => {
    const { container } = render(
      <MasterDetail detail={null} detailOpen={false} list={<p>Роли</p>} onBack={vi.fn()} />,
    )

    expect(container.querySelector('.rp-master-detail')).toHaveAttribute('data-detail-empty', 'true')
    expect(screen.getByRole('region', { name: 'Список' })).toHaveTextContent('Роли')
    expect(screen.queryByRole('region', { name: 'Детали' })).not.toBeInTheDocument()
  })
})

it('uses a two-column desktop layout and one visible pane below 900 px', () => {
  const source = readFileSync(resolve('src/design-system/layout/MasterDetail.css'), 'utf8')

  expect(source).toMatch(/\.rp-master-detail\s*\{[\s\S]*grid-template-columns:/)
  expect(source).toMatch(/@media \(max-width: 899px\)[\s\S]*data-detail-open='true'[\s\S]*\.rp-master-detail__list[\s\S]*display: none/)
  expect(source).toMatch(/@media \(max-width: 899px\)[\s\S]*data-detail-open='false'[\s\S]*\.rp-master-detail__detail[\s\S]*display: none/)
  expect(source).toMatch(/data-detail-empty='true'[\s\S]*grid-template-columns: minmax\(0, 1fr\)/)
})
