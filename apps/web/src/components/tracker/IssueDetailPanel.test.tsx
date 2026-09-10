import { readFileSync } from 'node:fs'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, type TrackerIssueDetail } from '../../api'
import { ru } from '../../i18n/ru'
import { IssueDetailPanel } from './IssueDetailPanel'

const workCss = readFileSync('src/domains/work/work.css', 'utf8')
const issue: TrackerIssueDetail = {
  key: 'ROBOPARK-42',
  summary: 'Робот не продолжает маршрут',
  status: 'Open',
  queue: 'ROBOPARK',
  robot: '447',
  url: 'https://st.yandex-team.ru/ROBOPARK-42',
  capabilities: {
    comment: false,
    assign: false,
    unassign: false,
    transition: false,
    close: false,
    attach: false,
  },
}

function declaredStyles(element: Element): Record<string, string> {
  const style = document.createElement('style')
  style.textContent = workCss
  document.head.append(style)

  const declarations: Record<string, string> = {}
  const rules = (style.sheet as CSSStyleSheet).cssRules
  for (const rule of Array.from(rules)) {
    if (!('selectorText' in rule && 'style' in rule)) continue
    const styleRule = rule as CSSStyleRule
    if (!element.matches(styleRule.selectorText)) continue
    for (const property of Array.from(styleRule.style)) {
      declarations[property] = styleRule.style.getPropertyValue(property)
    }
  }

  style.remove()
  return declarations
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('IssueDetailPanel', () => {
  it('exposes its robot-check link by purpose with a 44px Work target', () => {
    vi.spyOn(api, 'emergencyResolve').mockReturnValue(new Promise(() => {}))
    render(
      <MemoryRouter>
        <div className="rp-workbench">
          <IssueDetailPanel comments={[]} issue={issue} />
        </div>
      </MemoryRouter>,
    )

    const robotField = screen.getByText(ru.tracker.fields.robot, { selector: 'dt' }).parentElement
    expect(robotField).not.toBeNull()
    const link = within(robotField!).getByRole('link', { name: 'Проверить робота 447' })

    expect(link).toHaveAttribute('href', '/robots/447/check')
    expect(declaredStyles(link)).toMatchObject({
      display: 'inline-flex',
      'min-height': 'var(--rp-control-min-size)',
      'min-width': 'var(--rp-control-min-size)',
    })
    expect(screen.getAllByRole('link', { name: 'Проверить робота 447' })).toHaveLength(2)
  })

  it('uses a button for the robot when an in-context check callback is provided', () => {
    const onOpenRobotCheck = vi.fn()
    render(
      <MemoryRouter>
        <IssueDetailPanel comments={[]} issue={issue} onOpenRobotCheck={onOpenRobotCheck} />
      </MemoryRouter>,
    )

    const robotField = screen.getByText(ru.tracker.fields.robot, { selector: 'dt' }).parentElement
    const button = within(robotField!).getByRole('button', { name: 'Проверить робота 447' })
    button.click()

    expect(onOpenRobotCheck).toHaveBeenCalledOnce()
    expect(within(robotField!).queryByRole('link')).not.toBeInTheDocument()
  })

  it('renders the robot as plain text in read-only mode', () => {
    render(
      <MemoryRouter>
        <IssueDetailPanel comments={[]} issue={issue} robotReadOnly />
      </MemoryRouter>,
    )

    const robotField = screen.getByText(ru.tracker.fields.robot, { selector: 'dt' }).parentElement
    expect(robotField).toHaveTextContent('447')
    expect(within(robotField!).queryByRole('button')).not.toBeInTheDocument()
    expect(within(robotField!).queryByRole('link')).not.toBeInTheDocument()
  })
})
