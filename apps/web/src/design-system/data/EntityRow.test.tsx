/// <reference types="node" />

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { EntityRow } from './EntityRow'

describe('EntityRow', () => {
  it('names the status supplied for an entity', () => {
    render(
      <EntityRow
        actions={<button type="button">Открыть</button>}
        meta="Пост 4"
        status="В работе"
        title="Заявка №42"
      />,
    )

    expect(screen.getByText('Заявка №42')).toBeVisible()
    expect(screen.getByText('Пост 4')).toBeVisible()
    expect(screen.getByRole('group', { name: 'Статус' })).toHaveTextContent('В работе')
  })
})

it('gives interactive row actions tokenized 44 px targets', () => {
  const source = readFileSync(resolve('src/design-system/layout/MasterDetail.css'), 'utf8')

  expect(source).toMatch(/\.rp-entity-row__actions\s+:is\(button, \[role='button'\], a\)[\s\S]*min-block-size: var\(--rp-control-min-size\)/)
  expect(source).toMatch(/\.rp-entity-row__actions\s+:is\(button, \[role='button'\], a\)[\s\S]*min-inline-size: var\(--rp-control-min-size\)/)
})
